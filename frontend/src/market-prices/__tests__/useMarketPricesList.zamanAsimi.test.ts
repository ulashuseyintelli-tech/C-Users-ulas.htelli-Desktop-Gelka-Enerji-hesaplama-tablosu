// =============================================================================
// useMarketPricesList — SONLU ZAMAN AŞIMI testleri
// =============================================================================
// Kusur: `adminApi` axios örneğinde timeout yoktu (varsayılan 0 = sınırsız);
// sunucu yanıt vermezse yükleniyor durumu hiç bitmiyor, kullanıcı ne hata ne
// zaman aşımı görüyordu. Burada zaman aşımı ile KULLANICI/EFEKT İPTALİ
// ayrımının korunduğu kilitlenir.
// =============================================================================

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ListParams, MarketPricesListResponse } from '../types';

const mockListMarketPrices = vi.fn<[ListParams, AbortSignal?], Promise<MarketPricesListResponse>>();
vi.mock('../marketPricesApi', () => ({
  listMarketPrices: (...args: [ListParams, AbortSignal?]) => mockListMarketPrices(...args),
}));

import { useMarketPricesList } from '../hooks/useMarketPricesList';
import { OKUMA_ZAMAN_ASIMI_MS } from '../constants';

const PARAMS: ListParams = {
  page: 1, page_size: 20, sort_by: 'period', sort_order: 'desc', price_type: 'PTF',
};

/** axios'a sadık: signal abort edilince reddeder. */
function asiliIstek() {
  return (_p: ListParams, signal?: AbortSignal) =>
    new Promise<MarketPricesListResponse>((_coz, reddet) => {
      signal?.addEventListener('abort', () => {
        reddet(Object.assign(new DOMException('aborted', 'AbortError')));
      });
    });
}

beforeEach(() => { mockListMarketPrices.mockReset(); });
afterEach(() => { vi.useRealTimers(); });

describe('useMarketPricesList — zaman aşımı', () => {
  it('yanıtsız istek süre sonunda TIMEOUT hatası verir ve yükleniyor biter', async () => {
    vi.useFakeTimers();
    mockListMarketPrices.mockImplementation(asiliIstek());
    const { result } = renderHook(() => useMarketPricesList(PARAMS));
    await act(async () => {});
    expect(result.current.loading).toBe(true);

    await act(async () => { vi.advanceTimersByTime(OKUMA_ZAMAN_ASIMI_MS); });

    expect(result.current.loading).toBe(false);
    expect(result.current.error?.error_code).toBe('TIMEOUT');
    expect(result.current.error?.message).toContain('zaman aşımına');
  });

  it('süre dolmadan hata ÇIKMAZ', async () => {
    vi.useFakeTimers();
    mockListMarketPrices.mockImplementation(asiliIstek());
    const { result } = renderHook(() => useMarketPricesList(PARAMS));
    await act(async () => { vi.advanceTimersByTime(OKUMA_ZAMAN_ASIMI_MS - 1000); });
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it('EFEKT İPTALİ (unmount) hata ÜRETMEZ', async () => {
    mockListMarketPrices.mockImplementation(asiliIstek());
    const { result, unmount } = renderHook(() => useMarketPricesList(PARAMS));
    await act(async () => {});
    await act(async () => { unmount(); });
    expect(result.current.error).toBeNull();
  });

  it('başarılı yanıt zamanlayıcıyı temizler, hata bırakmaz', async () => {
    vi.useFakeTimers();
    mockListMarketPrices.mockResolvedValue({
      status: 'ok', total: 0, page: 1, page_size: 20, items: [],
    });
    const { result } = renderHook(() => useMarketPricesList(PARAMS));
    // Sahte zamanlayıcı altında waitFor kullanılmaz (kendisi zamanlayıcıya
    // dayanır); mikro görevleri act ile boşaltmak yeterli.
    await act(async () => {});
    await act(async () => {});
    expect(result.current.loading).toBe(false);
    await act(async () => { vi.advanceTimersByTime(OKUMA_ZAMAN_ASIMI_MS * 2); });
    expect(result.current.error).toBeNull();
  });
});
