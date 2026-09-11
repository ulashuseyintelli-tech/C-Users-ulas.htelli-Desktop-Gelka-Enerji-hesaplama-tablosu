import { describe, it, expect } from 'vitest';
import {
  evaluatePriceReadiness,
  priceSourceLabel,
  type PriceProvenance,
  type PriceReadinessInput,
} from '../priceReadiness';

// Fiyat Doğruluğu Faz 1 (owner teyidi): istemci kapısı sunucunun yansımasıdır.
// Eksik veri, gerçek sıfır, hariç, muaf ve provisional TASLAK ayrı durumlardır;
// kullanıcı onayı yolu YOKTUR.

const kesin: PriceProvenance = {
  version: 2,
  ptf: { status: 'matched', trusted: true, final: true, system_verified: true },
  yekdem: {
    mode: 'included',
    period_status: 'known',
    period_value: 235.63,
    period_record_status: 'final',
    period_trusted: true,
    period_verified: true,
  },
};

const provisionalKayit: PriceProvenance = {
  version: 2,
  ptf: { status: 'matched', trusted: true, final: false, system_verified: false },
  yekdem: {
    mode: 'included',
    period_status: 'known',
    period_value: 235.63,
    period_record_status: 'provisional',
    period_trusted: true,
    period_verified: false,
  },
};

const temel: PriceReadinessInput = {
  ptf: 2508.8,
  yekdem: 235.63,
  yekdemMode: 'included',
  provenance: kesin,
  valuesEditedByUser: false,
};

describe('evaluatePriceReadiness', () => {
  it('sunucuda kesin (final) doğrulanmış PTF ve YEKDEM → hazır, taslak değil', () => {
    const r = evaluatePriceReadiness(temel);
    expect(r.ready).toBe(true);
    expect(r.draft).toBe(false);
    expect(r.reasons).toEqual([]);
  });

  it('PTF bilinmiyor (null) → engel; 0 ile doldurulmaz', () => {
    const r = evaluatePriceReadiness({ ...temel, ptf: null });
    expect(r.ready).toBe(false);
    expect(r.ptfMissing).toBe(true);
  });

  it('YEKDEM bilinmiyor ve dahil → engel', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdem: null });
    expect(r.ready).toBe(false);
    expect(r.yekdemMissing).toBe(true);
  });

  it('hariç ve muaf açık seçimleri YEKDEM değeri istemez (PTF yine kesin olmalı)', () => {
    for (const mod of ['excluded', 'exempt'] as const) {
      const r = evaluatePriceReadiness({ ...temel, yekdem: null, yekdemMode: mod });
      expect(r.yekdemMissing).toBe(false);
      expect(r.ready).toBe(true);
    }
    const provisionalPtf = evaluatePriceReadiness({
      ...temel, yekdem: null, yekdemMode: 'exempt', provenance: provisionalKayit,
    });
    expect(provisionalPtf.ready).toBe(false);
  });

  it('YEKDEM seçimi yapılmadı (AI: faturada kalem yok) → engel; "hariç" tahmin edilmez', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdemMode: null });
    expect(r.modeRequired).toBe(true);
    expect(r.ready).toBe(false);
    expect(r.reasons.join(' ')).toContain('seçilmedi');
  });

  it('gerçek 0 YEKDEM eksik sayılmaz ama kesinleşemez; hariç/muaf ile karışmaz', () => {
    const sifir = evaluatePriceReadiness({ ...temel, yekdem: 0 });
    expect(sifir.yekdemMissing).toBe(false);
    expect(sifir.ready).toBe(false);
    expect(sifir.reasons.join(' ')).toContain('YEKDEM 0');
    // Aynı ekranda hariç seçimi 0 değerinden bağımsızdır.
    expect(evaluatePriceReadiness({ ...temel, yekdem: 0, yekdemMode: 'excluded' }).ready).toBe(true);
  });

  it('provisional PTF ve YEKDEM → TASLAK (provisional=true), kesin teklif için hazır değil', () => {
    const r = evaluatePriceReadiness({ ...temel, provenance: provisionalKayit });
    expect(r.ready).toBe(false);
    expect(r.draft).toBe(true);
    expect(r.provisional).toBe(true);
    expect(r.reasons.filter((m) => m.includes('provisional'))).toHaveLength(2);
  });

  it('dönem YEKDEM kesin ama ekrandaki değer farklı → doğrulanmadı', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdem: 240 });
    expect(r.ready).toBe(false);
    expect(r.provisional).toBe(false);
  });

  it('elle değiştirilen fiyat sunucu doğrulamasını geçersiz kılar', () => {
    const r = evaluatePriceReadiness({ ...temel, valuesEditedByUser: true });
    expect(r.ready).toBe(false);
    expect(r.draft).toBe(true);
  });

  it('kaynak bilgisi yok (provenance null) → taslak', () => {
    const r = evaluatePriceReadiness({ ...temel, provenance: null });
    expect(r.ready).toBe(false);
  });

  it('kullanıcı onayı girdisi yok: "onaylandı" beyanı hazırlığı değiştiremez', () => {
    const beyanli = { ...temel, provenance: null, userConfirmed: true } as unknown as PriceReadinessInput;
    expect(evaluatePriceReadiness(beyanli).ready).toBe(false);
    const provisionalBeyan = {
      ...temel, provenance: provisionalKayit, userConfirmed: true,
    } as unknown as PriceReadinessInput;
    expect(evaluatePriceReadiness(provisionalBeyan).ready).toBe(false);
  });
});

describe('priceSourceLabel', () => {
  it('bilinen kaynakları Türkçe etiketler; eski "default" etiketi yok', () => {
    expect(priceSourceLabel('manual_override')).toBe('manuel kayıt');
    expect(priceSourceLabel('reference_scalar')).toBe('aylık referans kaydı');
    expect(priceSourceLabel('hourly_weighted:duz')).toBe('saatlik ağırlıklı (profil)');
    expect(priceSourceLabel('not_found')).toBe('PTF yok');
    expect(priceSourceLabel(null)).toBe('kaynak yok');
  });
});
