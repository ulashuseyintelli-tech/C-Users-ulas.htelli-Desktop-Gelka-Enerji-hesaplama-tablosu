// =============================================================================
// EPDK / Incidents OKUMA isteklerine sonlu süre sınırı — hedefli test
// =============================================================================
// Kusur: `adminApi` global timeout'suz; bu okumalar sunucu yanıt vermezse
// sonsuza kadar bekliyordu. Düzeltme istek başına `OKUMA_ISTEK_AYARI` verir.
// Yazma isteği (updateIncidentStatus) bu ayarı ALMAMALI.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { getMock, patchMock } = vi.hoisted(() => ({ getMock: vi.fn(), patchMock: vi.fn() }));

vi.mock('axios', async () => {
  const gercek = await vi.importActual<typeof import('axios')>('axios');
  const ornek = {
    get: getMock, patch: patchMock, post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    defaults: { headers: { common: {} } },
  };
  return { ...gercek, default: { ...gercek.default, create: () => ornek } };
});

import {
  OKUMA_ISTEK_AYARI, getDistributionTariffs, lookupDistributionTariff,
  getIncidents, getIncident, getIncidentStats, updateIncidentStatus,
} from '../api';
import { OKUMA_ZAMAN_ASIMI_MS } from '../market-prices/constants';

beforeEach(() => {
  getMock.mockReset().mockResolvedValue({ data: {} });
  patchMock.mockReset().mockResolvedValue({ data: {} });
});

describe('OKUMA süre sınırı', () => {
  it('ayar sonlu ve anlaşılır mesajlıdır', () => {
    expect(OKUMA_ISTEK_AYARI.timeout).toBe(OKUMA_ZAMAN_ASIMI_MS);
    expect(OKUMA_ISTEK_AYARI.timeout).toBeGreaterThan(0);
    expect(OKUMA_ISTEK_AYARI.timeoutErrorMessage).toContain('zaman aşımına');
  });

  const okumalar: Array<[string, () => Promise<unknown>]> = [
    ['getDistributionTariffs (EPDK)', () => getDistributionTariffs()],
    ['lookupDistributionTariff (EPDK)', () => lookupDistributionTariff('Sanayi', 'OG', 'Çift Terim')],
    ['getIncidents', () => getIncidents({ limit: 100 })],
    ['getIncident', () => getIncident(1)],
    ['getIncidentStats', () => getIncidentStats()],
  ];

  for (const [ad, cagri] of okumalar) {
    it(`${ad} süre sınırıyla çağrılır`, async () => {
      await cagri();
      expect(getMock).toHaveBeenCalledTimes(1);
      expect(getMock.mock.calls[0][1]).toMatchObject({
        timeout: OKUMA_ZAMAN_ASIMI_MS,
        timeoutErrorMessage: expect.stringContaining('zaman aşımına'),
      });
    });
  }

  it('YAZMA isteği (updateIncidentStatus) okuma süre sınırını ALMAZ', async () => {
    await updateIncidentStatus(1, 'ACK');
    expect(patchMock).toHaveBeenCalledTimes(1);
    const ayar = patchMock.mock.calls[0][2] ?? {};
    expect(ayar).not.toHaveProperty('timeoutErrorMessage');
    expect(ayar.timeout).toBeUndefined();
  });
});
