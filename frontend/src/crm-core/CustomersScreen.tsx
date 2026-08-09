// S1 WB-5 — Müşteriler listesi: Firma/Telefon/E-posta/Açık teklif/Son
// teklif tarihi/Durum + arama. Satıra tıklayınca Müşteri Detay açılır
// (CustomerDetailScreen). Activity timeline YOK (S2'ye ertelendi).
import { useCallback, useEffect, useState } from 'react';
import { Search, Loader2, AlertCircle } from 'lucide-react';
import { listCustomers, CustomerListItem } from '../api';
import { CustomerDetailScreen } from './CustomerDetailScreen';

interface CustomersScreenProps {
  selectedCustomerId: number | null;
  onSelectCustomer: (id: number) => void;
  onCloseDetail: () => void;
}

function formatDateTr(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('tr-TR');
  } catch {
    return '—';
  }
}

export function CustomersScreen({ selectedCustomerId, onSelectCustomer, onCloseDetail }: CustomersScreenProps) {
  const [customers, setCustomers] = useState<CustomerListItem[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (searchTerm: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await listCustomers({ search: searchTerm || undefined, limit: 100 });
      setCustomers(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Müşteriler yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load(search), 300); // basit arama debounce'u
    return () => clearTimeout(timer);
  }, [search, load]);

  if (selectedCustomerId !== null) {
    return <CustomerDetailScreen customerId={selectedCustomerId} onBack={onCloseDetail} />;
  }

  return (
    <div className="space-y-4">
      <div className="relative max-w-md">
        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Firma, unvan veya e-posta ara..."
          className="input pl-9 w-full"
        />
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
        ) : customers.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            {search ? 'Aramayla eşleşen müşteri bulunamadı.' : 'Henüz müşteri kaydı yok.'}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="px-4 py-2 font-medium">Firma</th>
                <th className="px-4 py-2 font-medium">Telefon</th>
                <th className="px-4 py-2 font-medium">E-posta</th>
                <th className="px-4 py-2 font-medium">Açık teklif</th>
                <th className="px-4 py-2 font-medium">Son teklif tarihi</th>
                <th className="px-4 py-2 font-medium">Durum</th>
              </tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => onSelectCustomer(c.id)}
                  className="border-b border-gray-100 last:border-0 hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-2 font-medium text-gray-900">{c.company || c.name}</td>
                  <td className="px-4 py-2 text-gray-600">{c.phone || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{c.email || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{c.open_offer_count}</td>
                  <td className="px-4 py-2 text-gray-600">{formatDateTr(c.last_offer_at)}</td>
                  <td className="px-4 py-2">
                    {c.open_offer_count > 0 ? (
                      <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-green-100 text-green-700">
                        Açık teklifi var
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-gray-100 text-gray-600">
                        Teklif yok
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
