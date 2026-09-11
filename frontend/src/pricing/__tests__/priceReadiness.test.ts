import { describe, it, expect } from 'vitest';
import {
  evaluatePriceReadiness,
  priceSourceLabel,
  YEKDEM_MODE_LABELS,
  type PriceProvenance,
  type PriceReadinessInput,
} from '../priceReadiness';

// Fiyat Doğruluğu Faz 1 (owner teyidi): istemci kapısı sunucunun yansımasıdır.
// Eksik veri, açık (kesin) sıfır, anlamı bilinmeyen sıfır, hariç ve provisional TASLAK
// ayrı durumlardır; "muaf" seçeneği ve kullanıcı onayı yolu YOKTUR.

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

  it('hariç açık seçimi YEKDEM değeri istemez (PTF yine kesin olmalı); "muaf" seçeneği yok', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdem: null, yekdemMode: 'excluded' });
    expect(r.yekdemMissing).toBe(false);
    expect(r.ready).toBe(true);
    const provisionalPtf = evaluatePriceReadiness({
      ...temel, yekdem: null, yekdemMode: 'excluded', provenance: provisionalKayit,
    });
    expect(provisionalPtf.ready).toBe(false);
    expect(Object.keys(YEKDEM_MODE_LABELS)).toEqual(['included', 'excluded']);
  });

  it('YEKDEM seçimi yapılmadı (AI: faturada kalem yok) → engel; "hariç" tahmin edilmez', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdemMode: null });
    expect(r.modeRequired).toBe(true);
    expect(r.ready).toBe(false);
    expect(r.reasons.join(' ')).toContain('seçilmedi');
  });

  it('açık giriş kaydı olmayan (anlamı bilinmeyen) 0 eksik sayılmaz ama kesinleşemez', () => {
    const eskiSifir: PriceProvenance = {
      ...kesin,
      yekdem: {
        ...kesin.yekdem, period_status: 'zero_unverified', period_value: 0,
        period_trusted: false, period_verified: false, period_zero_audit_id: null,
      },
    };
    const sifir = evaluatePriceReadiness({ ...temel, yekdem: 0, provenance: eskiSifir });
    expect(sifir.yekdemMissing).toBe(false);
    expect(sifir.ready).toBe(false);
    expect(sifir.reasons.join(' ')).toContain('YEKDEM 0');
    // Aynı ekranda hariç seçimi 0 değerinden bağımsızdır.
    expect(evaluatePriceReadiness({
      ...temel, yekdem: 0, yekdemMode: 'excluded', provenance: eskiSifir,
    }).ready).toBe(true);
  });

  it('yetkili ekrandan açık ve kesin girilmiş gerçek 0 hazırdır; ekrandaki farklı değer değildir', () => {
    const acikSifir: PriceProvenance = {
      ...kesin,
      yekdem: { ...kesin.yekdem, period_value: 0, period_zero_audit_id: 7 },
    };
    const r = evaluatePriceReadiness({ ...temel, yekdem: 0, provenance: acikSifir });
    expect(r.ready).toBe(true);
    expect(r.yekdemMissing).toBe(false);
    expect(evaluatePriceReadiness({ ...temel, yekdem: 5, provenance: acikSifir }).ready).toBe(false);
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
