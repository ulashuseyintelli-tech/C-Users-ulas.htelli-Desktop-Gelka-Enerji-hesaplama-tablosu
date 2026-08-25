// S1 WB-6 — Teklifler listesi: Firma/Tarih/Tutar/Katsayı/Durum/Sözleşmeye
// dönüştü mü + filtreler. Durum değişikliği YALNIZ backend'in
// allowed_transitions'ının izin verdiği aksiyon butonlarıyla yapılır —
// serbest enum dropdown YOK (owner kararı, S1 preflight).
//
// "Sözleşmeye dönüştü mü" sorusunun otoritesi Contract tablosudur
// (has_contract, backend'de hesaplanır) — Offer.status TEK BAŞINA
// güvenilir değildir (best-effort senkron, bkz. api.ts OfferListItem).
// S5-R01 — Liste ticari alanlarla zenginleştirildi (Tedarikçi/Dönem/Tüketim/
// Mevcut Tutar/Tasarruf/Tasarruf Oranı) ve satırdan teklif detayı açılabilir.
// Tüm değerler `GET /offers` snapshot'ından gelir; ekranda HESAP YAPILMAZ.
import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';
import { listOffers, updateOfferStatus, OfferListItem } from '../api';
import { QuickCrmActions } from './QuickCrmActions';
import { OfferDetailModal } from './OfferDetailModal';

const STATUS_LABELS: Record<string, string> = {
  draft: 'Taslak',
  sent: 'Gönderildi',
  viewed: 'Görüntülendi',
  accepted: 'Kabul',
  rejected: 'Red',
  contracting: 'Sözleşme',
  completed: 'Tamamlandı',
  expired: 'Süresi Doldu',
};

const STATUS_STYLES: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700',
  sent: 'bg-blue-100 text-blue-700',
  viewed: 'bg-indigo-100 text-indigo-700',
  accepted: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
  contracting: 'bg-amber-100 text-amber-700',
  completed: 'bg-emerald-100 text-emerald-700',
  expired: 'bg-gray-100 text-gray-500',
};

// S1 owner kararı: filtre seçenekleri yalnız bu 6 durum (viewed/expired
// filtre listesine dahil değil — basitlik, owner'ın kendi seçimi).
const FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'draft', label: 'Taslak' },
  { value: 'sent', label: 'Gönderildi' },
  { value: 'accepted', label: 'Kabul' },
  { value: 'rejected', label: 'Red' },
  { value: 'contracting', label: 'Sözleşme' },
  { value: 'completed', label: 'Tamamlandı' },
];

const TRANSITION_ACTION_LABELS: Record<string, string> = {
  sent: 'Gönderildi Olarak İşaretle',
  viewed: 'Görüntülendi Olarak İşaretle',
  accepted: 'Kabul Edildi',
  rejected: 'Reddedildi',
  expired: 'Süresi Doldu Olarak İşaretle',
  contracting: 'Sözleşme Sürecine Al',
  completed: 'Tamamlandı Olarak İşaretle',
};

function formatDateTr(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('tr-TR');
  } catch {
    return '—';
  }
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 }).format(value);
}

function formatKwh(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 0 }).format(value);
}

// Backend `savings_ratio`yu KESİR olarak saklar (calculator.py: 0..1).
function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `%${(value * 100).toFixed(1)}`;
}

export function OffersScreen() {
  const [offers, setOffers] = useState<OfferListItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  // S5-R01: acik teklif detay modali (null = kapali)
  const [detailOfferId, setDetailOfferId] = useState<number | null>(null);

  const load = useCallback(async (status: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await listOffers({ status: status || undefined, limit: 100 });
      setOffers(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Teklifler yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(statusFilter);
  }, [statusFilter, load]);

  const handleTransition = async (offerId: number, newStatus: string) => {
    setUpdatingId(offerId);
    setError(null);
    try {
      await updateOfferStatus(offerId, newStatus);
      await load(statusFilter);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Durum güncellenemedi.');
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setStatusFilter('')}
          className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
            statusFilter === '' ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          Tümü
        </button>
        {FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setStatusFilter(opt.value)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
              statusFilter === opt.value ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      <div className="card overflow-x-auto">
        {loading ? (
          <div className="p-8 text-center text-gray-500">
            <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
            Yükleniyor...
          </div>
        ) : offers.length === 0 ? (
          <div className="p-8 text-center text-gray-500">Bu filtreyle eşleşen teklif bulunamadı.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="px-4 py-2 font-medium">Firma</th>
                <th className="px-4 py-2 font-medium">Tedarikçi</th>
                <th className="px-4 py-2 font-medium">Dönem</th>
                <th className="px-4 py-2 font-medium text-right">Tüketim (kWh)</th>
                <th className="px-4 py-2 font-medium text-right">Mevcut Tutar</th>
                <th className="px-4 py-2 font-medium text-right">Teklif Tutarı</th>
                <th className="px-4 py-2 font-medium text-right">Tasarruf</th>
                <th className="px-4 py-2 font-medium text-right">Tasarruf Oranı</th>
                <th className="px-4 py-2 font-medium text-right">Katsayı</th>
                <th className="px-4 py-2 font-medium">Tarih</th>
                <th className="px-4 py-2 font-medium">Durum</th>
                <th className="px-4 py-2 font-medium">Sözleşmeye Dönüştü mü?</th>
                <th className="px-4 py-2 font-medium">Detay</th>
                <th className="px-4 py-2 font-medium">Aksiyon</th>
                <th className="px-4 py-2 font-medium">CRM</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id} className="border-b border-gray-100 last:border-0">
                  <td className="px-4 py-2 font-medium text-gray-900">{o.customer_name || 'Müşterisiz'}</td>
                  <td className="px-4 py-2 text-gray-600">{o.vendor || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{o.invoice_period || '—'}</td>
                  <td className="px-4 py-2 text-gray-600 text-right">{formatKwh(o.consumption_kwh)}</td>
                  <td className="px-4 py-2 text-gray-600 text-right">{formatCurrency(o.current_total)}</td>
                  <td className="px-4 py-2 text-gray-900 font-medium text-right">{formatCurrency(o.offer_total)}</td>
                  <td className="px-4 py-2 text-green-700 text-right">{formatCurrency(o.savings_amount)}</td>
                  <td className="px-4 py-2 text-green-700 text-right">{formatRatio(o.savings_ratio)}</td>
                  <td className="px-4 py-2 text-gray-600 text-right">{o.agreement_multiplier.toFixed(2)}</td>
                  <td className="px-4 py-2 text-gray-600">{formatDateTr(o.created_at)}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        STATUS_STYLES[o.status] || 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {STATUS_LABELS[o.status] || o.status}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    {o.has_contract ? (
                      <span className="text-green-700 font-medium">Evet</span>
                    ) : (
                      <span className="text-gray-400">Hayır</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <button
                      type="button"
                      onClick={() => setDetailOfferId(o.id)}
                      className="btn-secondary text-xs px-2 py-1"
                    >
                      Detay
                    </button>
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex flex-wrap gap-1.5">
                      {o.allowed_transitions.length === 0 ? (
                        <span className="text-gray-400 text-xs">—</span>
                      ) : (
                        o.allowed_transitions.map((target) => (
                          <button
                            key={target}
                            disabled={updatingId === o.id}
                            onClick={() => handleTransition(o.id, target)}
                            className="btn-secondary text-xs px-2 py-1 disabled:opacity-50"
                          >
                            {updatingId === o.id ? '...' : TRANSITION_ACTION_LABELS[target] || target}
                          </button>
                        ))
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2">
                    <QuickCrmActions subject={{ offer_id: o.id }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detailOfferId !== null && (
        <OfferDetailModal offerId={detailOfferId} onClose={() => setDetailOfferId(null)} />
      )}
    </div>
  );
}
