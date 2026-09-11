// =============================================================================
// Fiyat Doğruluğu Faz 1 — manuel teklifte dönem PTF/YEKDEM çekimi (yarış korumalı)
// =============================================================================
// - Dönem/profil/tarife/firma değişince ÖNCE onStart çağrılır: önceki dönemin
//   fiyatı ekranda KALMAZ (eski fiyatla teklif riski).
// - Gecikmiş yanıt YENİ dönemi ezmez: her istek kendi effect'ine bağlıdır; effect
//   temizlenince (bağımlılık değişti) istek iptal edilir ve yanıtı yok sayılır.
// - Hata → onError; fiyatlar onStart'ta temizlendiği için eski değer kullanılmaz.
// - EPİAŞ otomatik çekimi Faz 1'de kapalı: auto_fetch parametresi GÖNDERİLMEZ.
//
// Çağrıldığı yerler:
// - App.tsx → manuel mod fiyat paneli (GET /api/epias/prices/{period})
// =============================================================================

import { useEffect, useRef } from 'react';
import { API_BASE, type EpiasPricesResponse } from '../api';

export interface PeriodPriceFetchOptions {
  enabled: boolean;
  period: string;
  profile: string;
  tariffGroup?: string;
  customerId?: string;
  /** Aynı dönemi yeniden çekmek için (ör. fiyat kaydı sonrası) */
  refreshKey?: number;
  onStart: () => void;
  onResult: (response: EpiasPricesResponse) => void;
  onError: (message: string) => void;
  /** İstek bittiğinde ya da iptal edildiğinde (yükleniyor göstergesi için) */
  onSettled?: () => void;
  /** Test için enjekte edilebilir fetch */
  fetchImpl?: typeof fetch;
}

export function buildPeriodPricesUrl(
  period: string,
  profile: string,
  tariffGroup?: string,
  customerId?: string,
): string {
  const qs = new URLSearchParams({ profile });
  if (tariffGroup) qs.append('tariff_group', tariffGroup);
  if (customerId) qs.append('customer_id', customerId);
  return `${API_BASE}/api/epias/prices/${encodeURIComponent(period)}?${qs.toString()}`;
}

function iptalMi(err: unknown): boolean {
  return typeof err === 'object' && err !== null && (err as { name?: string }).name === 'AbortError';
}

export function usePeriodPriceFetch(options: PeriodPriceFetchOptions): void {
  const guncel = useRef(options);
  guncel.current = options;
  const { enabled, period, profile, tariffGroup, customerId, refreshKey } = options;

  useEffect(() => {
    if (!enabled || !period) return;
    const controller = new AbortController();
    let gecersiz = false;
    let bitti = false;
    const bitir = () => {
      if (!bitti) {
        bitti = true;
        guncel.current.onSettled?.();
      }
    };

    guncel.current.onStart();
    const doFetch = guncel.current.fetchImpl ?? fetch;
    (async () => {
      try {
        const res = await doFetch(buildPeriodPricesUrl(period, profile, tariffGroup, customerId), {
          signal: controller.signal,
        });
        if (!res.ok) {
          const govde = await res.json().catch(() => ({}));
          const detay = govde && typeof govde.detail === 'string' ? govde.detail : '';
          throw new Error(detay || `HTTP ${res.status}`);
        }
        const govde = (await res.json()) as EpiasPricesResponse;
        if (gecersiz) return; // gecikmiş yanıt: yeni dönemi EZMEZ
        guncel.current.onResult(govde);
      } catch (err: unknown) {
        if (gecersiz || iptalMi(err)) return;
        guncel.current.onError(err instanceof Error && err.message ? err.message : 'Fiyat çekilemedi');
      } finally {
        if (!gecersiz) bitir();
      }
    })();

    return () => {
      gecersiz = true;
      controller.abort();
      bitir();
    };
  }, [enabled, period, profile, tariffGroup, customerId, refreshKey]);
}
