// S1 WB-7 — Sözleşmeler listesi: Firma(veya Müşterisiz)/Teklif/Başlangıç/
// Bitiş/gerçek status/Katsayı/PDF. GET /api/contracts canonical endpoint
// (WB-2) — Customer Detay'daki alt-sekme de AYNI fonksiyonu kullanıyor.
//
// Durum etiketleri BİLEREK yalnız kodda GERÇEKTEN üretilen 4 değeri
// kapsıyor (DRAFT/READY_TO_GENERATE/FINALIZING/FINALIZED) — docstring'deki
// kullanılmayan durumlar (DOCUMENTS_UPLOADED vb.) UI'a taşınmadı (owner
// kararı, S1 preflight).
import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertCircle, Download } from 'lucide-react';
import { listContracts, downloadContractPdf, ContractOut } from '../contracts/contractsApi';

const STATUS_LABELS: Record<string, string> = {
  DRAFT: 'Taslak',
  READY_TO_GENERATE: 'Üretime Hazır',
  FINALIZING: 'İşleniyor',
  FINALIZED: 'Tamamlandı',
};

const STATUS_STYLES: Record<string, string> = {
  DRAFT: 'bg-gray-100 text-gray-700',
  READY_TO_GENERATE: 'bg-blue-100 text-blue-700',
  FINALIZING: 'bg-amber-100 text-amber-700',
  FINALIZED: 'bg-green-100 text-green-700',
};

function formatDateTr(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('tr-TR');
  } catch {
    return '—';
  }
}

export function ContractsScreen() {
  const [contracts, setContracts] = useState<ContractOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listContracts({ limit: 100 });
      setContracts(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Sözleşmeler yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDownload = async (contractId: number) => {
    setDownloadingId(contractId);
    setError(null);
    try {
      await downloadContractPdf(contractId, `sozlesme_${contractId}.pdf`);
    } catch (err: any) {
      setError(err?.message || 'PDF indirilemedi.');
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="space-y-4">
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
        ) : contracts.length === 0 ? (
          <div className="p-8 text-center text-gray-500">Henüz sözleşme kaydı yok.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="px-4 py-2 font-medium">Firma</th>
                <th className="px-4 py-2 font-medium">Teklif</th>
                <th className="px-4 py-2 font-medium">Başlangıç</th>
                <th className="px-4 py-2 font-medium">Bitiş</th>
                <th className="px-4 py-2 font-medium">Durum</th>
                <th className="px-4 py-2 font-medium">Katsayı</th>
                <th className="px-4 py-2 font-medium">PDF</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr key={c.id} className="border-b border-gray-100 last:border-0">
                  <td className="px-4 py-2 font-medium text-gray-900">{c.customer_name || 'Müşterisiz'}</td>
                  <td className="px-4 py-2 text-gray-600">#{c.offer_id}</td>
                  <td className="px-4 py-2 text-gray-600">{formatDateTr(c.start_date)}</td>
                  <td className="px-4 py-2 text-gray-600">{formatDateTr(c.end_date)}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        STATUS_STYLES[c.status] || 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {STATUS_LABELS[c.status] || c.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-gray-600">
                    {c.agreement_multiplier !== null ? c.agreement_multiplier.toFixed(2) : '—'}
                  </td>
                  <td className="px-4 py-2">
                    {c.status === 'FINALIZED' ? (
                      <button
                        onClick={() => handleDownload(c.id)}
                        disabled={downloadingId === c.id}
                        className="btn-secondary text-xs px-2 py-1 flex items-center gap-1 disabled:opacity-50"
                      >
                        <Download className="w-3 h-3" />
                        {downloadingId === c.id ? 'İndiriliyor...' : 'İndir'}
                      </button>
                    ) : (
                      <span className="text-gray-400 text-xs">—</span>
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
