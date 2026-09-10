import { describe, it, expect } from 'vitest';
import { evaluatePriceReadiness, priceSourceLabel, type PriceProvenance } from '../priceReadiness';

// Fiyat Doğruluğu Faz 1 — istemci kapısı: eksik veri / gerçek sıfır / YEKDEM hariç / onay.

const sistemDogrulamis: PriceProvenance = {
  ptf: { system_verified: true },
  yekdem: { system_verified: true, mode: 'included' },
};

const temel = {
  ptf: 2508.8 as number | null,
  yekdem: 235.63 as number | null,
  yekdemIncluded: true,
  provenance: sistemDogrulamis as PriceProvenance | null,
  valuesEditedByUser: false,
  userConfirmed: false,
};

describe('evaluatePriceReadiness', () => {
  it('sistemce doğrulanmış PTF ve YEKDEM → onaysız hazır', () => {
    const r = evaluatePriceReadiness(temel);
    expect(r.ready).toBe(true);
    expect(r.requiresConfirmation).toBe(false);
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

  it("'YEKDEM hariç' açık seçimi → YEKDEM gerekmez", () => {
    const r = evaluatePriceReadiness({ ...temel, yekdem: null, yekdemIncluded: false });
    expect(r.yekdemMissing).toBe(false);
    expect(r.ready).toBe(true);
  });

  it('gerçek 0 YEKDEM eksik sayılmaz ama HER ZAMAN açık onay ister', () => {
    const onaysiz = evaluatePriceReadiness({ ...temel, yekdem: 0 });
    expect(onaysiz.yekdemMissing).toBe(false);
    expect(onaysiz.requiresConfirmation).toBe(true);
    expect(onaysiz.ready).toBe(false);
    expect(evaluatePriceReadiness({ ...temel, yekdem: 0, userConfirmed: true }).ready).toBe(true);
  });

  it('kullanıcı değeri değiştirdiyse sunucu doğrulaması geçersiz → onay gerekir', () => {
    const r = evaluatePriceReadiness({ ...temel, valuesEditedByUser: true });
    expect(r.requiresConfirmation).toBe(true);
    expect(r.ready).toBe(false);
    expect(evaluatePriceReadiness({ ...temel, valuesEditedByUser: true, userConfirmed: true }).ready).toBe(true);
  });

  it('kaynak bilgisi yok (provenance null) → onay gerekir', () => {
    const r = evaluatePriceReadiness({ ...temel, provenance: null });
    expect(r.requiresConfirmation).toBe(true);
    expect(r.ready).toBe(false);
  });

  it('eksik fiyatta onay kutusu işe yaramaz (eksik, onayla doldurulamaz)', () => {
    const r = evaluatePriceReadiness({ ...temel, ptf: null, userConfirmed: true });
    expect(r.ready).toBe(false);
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
