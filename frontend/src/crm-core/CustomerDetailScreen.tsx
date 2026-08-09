// S1 WB-5 — Müşteri Detay: Genel Bilgiler / Teklifler / Sözleşmeler
// alt-sekmeleri. Activity timeline YOK (S2'ye ertelendi, owner kararı).
//
// Sözleşmeler alt-sekmesi, S1 canonical sorgu kararı gereği AYRI bir
// "customer contracts" endpoint'i değil, genel listContracts()'ı
// customer_id filtresiyle çağırır (bkz. contractsApi.ts).
import { useEffect, useState } from 'react';
import { ArrowLeft, Loader2, AlertCircle } from 'lucide-react';
import { getCustomer, CustomerDetail } from '../api';
import { listContracts, ContractOut } from '../contracts/contractsApi';
import { ActivityTimeline } from './ActivityTimeline';
import { TaskList } from './TaskList';

type DetailTab = 'general' | 'offers' | 'contracts' | 'activities' | 'tasks';

interface CustomerDetailScreenProps {
  customerId: number;
  onBack: () => void;
}

function formatDateTr(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('tr-TR');
  } catch {
    return '—';
  }
}

export function CustomerDetailScreen({ customerId, onBack }: CustomerDetailScreenProps) {
  const [tab, setTab] = useState<DetailTab>('general');
  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [contracts, setContracts] = useState<ContractOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [contractsLoading, setContractsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCustomer(null);
    setContracts(null);
    setTab('general');
    getCustomer(customerId)
      .then((data) => {
        if (!cancelled) setCustomer(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.response?.data?.detail?.message || err?.message || 'Müşteri yüklenemedi.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [customerId]);

  useEffect(() => {
    if (tab !== 'contracts' || contracts !== null) return;
    let cancelled = false;
    setContractsLoading(true);
    listContracts({ customer_id: customerId })
      .then((data) => {
        if (!cancelled) setContracts(data);
      })
      .catch(() => {
        // Sözleşme listesi yüklenemezse sessizce boş göster — genel
        // Müşteri Detay ekranını (Genel Bilgiler/Teklifler) kırmasın.
        if (!cancelled) setContracts([]);
      })
      .finally(() => {
        if (!cancelled) setContractsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, customerId, contracts]);

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
        Yükleniyor...
      </div>
    );
  }

  if (error || !customer) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="btn-secondary flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> Müşteriler listesine dön
        </button>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error || 'Müşteri bulunamadı.'}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="btn-secondary flex items-center gap-2">
        <ArrowLeft className="w-4 h-4" /> Müşteriler listesine dön
      </button>

      <div className="card p-4">
        <h2 className="text-lg font-bold text-gray-900">{customer.company || customer.name}</h2>
        {customer.company && <p className="text-sm text-gray-500">{customer.name}</p>}
      </div>

      <div className="border-b border-gray-200">
        <nav className="flex gap-6">
          {(
            [
              ['general', 'Genel Bilgiler'],
              ['offers', `Teklifler (${customer.offers.length})`],
              ['contracts', 'Sözleşmeler'],
              ['activities', 'Aktiviteler'],
              ['tasks', 'Görevler'],
            ] as [DetailTab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`py-3 px-2 border-b-2 font-medium text-sm transition-colors ${
                tab === key
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'general' && (
        <div className="card p-4 space-y-2 text-sm">
          <div>
            <span className="text-gray-500">Telefon:</span> {customer.phone || '—'}
          </div>
          <div>
            <span className="text-gray-500">E-posta:</span> {customer.email || '—'}
          </div>
          <div>
            <span className="text-gray-500">Adres:</span> {customer.address || '—'}
          </div>
          <div>
            <span className="text-gray-500">Notlar:</span> {customer.notes || '—'}
          </div>
          <div>
            <span className="text-gray-500">Kayıt tarihi:</span> {formatDateTr(customer.created_at)}
          </div>
        </div>
      )}

      {tab === 'offers' && (
        <div className="card overflow-x-auto">
          {customer.offers.length === 0 ? (
            <div className="p-8 text-center text-gray-500">Bu müşterinin henüz teklifi yok.</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-gray-500">
                  <th className="px-4 py-2 font-medium">Dönem</th>
                  <th className="px-4 py-2 font-medium">Tasarruf</th>
                  <th className="px-4 py-2 font-medium">Durum</th>
                  <th className="px-4 py-2 font-medium">Tarih</th>
                </tr>
              </thead>
              <tbody>
                {customer.offers.map((o) => (
                  <tr key={o.id} className="border-b border-gray-100 last:border-0">
                    <td className="px-4 py-2">{o.invoice_period || '—'}</td>
                    <td className="px-4 py-2">%{(o.savings_ratio * 100).toFixed(1)}</td>
                    <td className="px-4 py-2">{o.status}</td>
                    <td className="px-4 py-2">{formatDateTr(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'contracts' && (
        <div className="card overflow-x-auto">
          {contractsLoading ? (
            <div className="p-8 text-center text-gray-500">
              <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
              Yükleniyor...
            </div>
          ) : !contracts || contracts.length === 0 ? (
            <div className="p-8 text-center text-gray-500">Bu müşterinin henüz sözleşmesi yok.</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-gray-500">
                  <th className="px-4 py-2 font-medium">Sözleşme No</th>
                  <th className="px-4 py-2 font-medium">Durum</th>
                  <th className="px-4 py-2 font-medium">Başlangıç</th>
                  <th className="px-4 py-2 font-medium">Bitiş</th>
                </tr>
              </thead>
              <tbody>
                {contracts.map((c) => (
                  <tr key={c.id} className="border-b border-gray-100 last:border-0">
                    <td className="px-4 py-2">{c.contract_number || `#${c.id}`}</td>
                    <td className="px-4 py-2">{c.status}</td>
                    <td className="px-4 py-2">{formatDateTr(c.start_date)}</td>
                    <td className="px-4 py-2">{formatDateTr(c.end_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'activities' && <ActivityTimeline subject={{ customer_id: customerId }} />}
      {tab === 'tasks' && <TaskList subject={{ customer_id: customerId }} />}
    </div>
  );
}
