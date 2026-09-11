import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { MarketPriceRecord, UpsertMarketPriceRequest } from '../types';

// Fiyat Doğruluğu Faz 1 (K3) — PTF/YEKDEM girişi mevcut yetkili yönetim ekranında.

const submit = vi.fn<[UpsertMarketPriceRequest], Promise<unknown>>();

vi.mock('../hooks/useUpsertMarketPrice', () => ({
  useUpsertMarketPrice: () => ({ submit, loading: false, error: null, fieldErrors: {} }),
}));

import { UpsertFormModal } from '../UpsertFormModal';

function ac(editingRecord?: MarketPriceRecord) {
  return render(
    <UpsertFormModal open onClose={vi.fn()} onSuccess={vi.fn()} onToast={vi.fn()}
                     editingRecord={editingRecord} />,
  );
}

function doldur(etiketId: string, deger: string) {
  fireEvent.change(document.getElementById(etiketId)!, { target: { value: deger } });
}

const kayit: MarketPriceRecord = {
  period: '2026-07', ptf_tl_per_mwh: 2699.61, status: 'provisional', price_type: 'PTF',
  captured_at: '', updated_at: '', updated_by: '', source: 'epias_manual', source_note: '',
  change_reason: '', is_locked: false, yekdem_tl_per_mwh: 0,
};

describe('UpsertFormModal — YEKDEM alanı', () => {
  beforeEach(() => {
    submit.mockReset();
    submit.mockResolvedValue({ status: 'ok', action: 'created', period: '2026-07', warnings: [] });
  });

  it('yeni kayıtta YEKDEM girilir ve istek yekdem_value taşır', async () => {
    ac();
    doldur('upsert-period', '2026-07');
    doldur('upsert-value', '2699,61');
    doldur('upsert-yekdem', '486,31');
    fireEvent.click(screen.getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toMatchObject({ period: '2026-07', value: 2699.61, yekdem_value: 486.31 });
  });

  it('yeni kayıtta YEKDEM boşsa gönderilmez ve alan hatası gösterilir', () => {
    ac();
    doldur('upsert-period', '2026-07');
    doldur('upsert-value', '2699.61');
    fireEvent.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByTestId('error-yekdem_value')).toHaveTextContent('YEKDEM zorunlu');
  });

  it('gerçek 0 YEKDEM açık değer olarak gönderilir (boş alan 0 sayılmaz)', async () => {
    ac();
    doldur('upsert-period', '2026-07');
    doldur('upsert-value', '2699.61');
    doldur('upsert-yekdem', '0');
    fireEvent.click(screen.getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).toMatchObject({ period: '2026-07', yekdem_value: 0 });
    expect(screen.queryByTestId('yekdem-zero-hint')).toBeNull(); // yeni kayıtta ipucu yok
  });

  it('negatif YEKDEM kabul edilmez', () => {
    ac();
    doldur('upsert-period', '2026-07');
    doldur('upsert-value', '2699.61');
    doldur('upsert-yekdem', '-5');
    fireEvent.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByTestId('error-yekdem_value')).toHaveTextContent('0 ya da pozitif');
  });

  it('güncellemede YEKDEM boş bırakılırsa istek yekdem_value içermez (mevcut korunur)', async () => {
    ac(kayit);
    // Kayıtlı 0 önceden doldurulmaz: eski sıfır farkında olmadan "açık sıfır" teyidine dönmez.
    expect((document.getElementById('upsert-yekdem') as HTMLInputElement).value).toBe('');
    expect(screen.getByTestId('yekdem-zero-hint')).toHaveTextContent('açıkça 0 yazın');
    doldur('upsert-change-reason', 'PTF düzeltme');
    fireEvent.click(screen.getByRole('button', { name: 'Güncelle' }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0]).not.toHaveProperty('yekdem_value');
  });

  it('durum alanı yalnız kesinleşmiş kaydın kesin teklifte kullanıldığını söyler', () => {
    ac();
    const ipucu = screen.getByTestId('status-hint');
    expect(ipucu).toHaveTextContent('Kesinleşmiş');
    expect(ipucu).toHaveTextContent('kesin teklif ve PDF');
    expect(ipucu).toHaveTextContent('yalnız taslak');
  });
});
