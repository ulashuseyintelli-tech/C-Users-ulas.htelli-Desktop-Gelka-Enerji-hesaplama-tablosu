// S1 WB-8 — Bugün: yalnız mevcut Customer/Offer/Contract verisinden basit
// türetilmiş özet kartları (owner kararı — Activity/Task S1'de yok, ayrı
// bir "today" tablosu yok, "3 gündür cevap bekliyor" gibi iş kuralı icat
// edilmedi; bu S2'nin kapsamı).
import { useEffect, useState } from 'react';
import { Loader2, AlertCircle, Users, FileText, CheckCircle2, FileCheck } from 'lucide-react';
import { getStats, StatsResponse } from '../api';

export function TodayScreen() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStats()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.response?.data?.detail?.message || err?.message || 'Özet yüklenemedi.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
        Yükleniyor...
      </div>
    );
  }

  if (error || !stats) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
        <p className="text-sm text-red-700">{error || 'Özet yüklenemedi.'}</p>
      </div>
    );
  }

  const cards = [
    { label: 'Toplam Müşteri', value: stats.total_customers, icon: Users, color: 'text-blue-600 bg-blue-50' },
    { label: 'Açık Teklifler', value: stats.total_open_offers, icon: FileText, color: 'text-amber-600 bg-amber-50' },
    {
      label: 'Kabul Edilen Teklifler',
      value: stats.offers_by_status['accepted'] || 0,
      icon: CheckCircle2,
      color: 'text-green-600 bg-green-50',
    },
    {
      label: 'Tamamlanmış Sözleşmeler',
      value: stats.total_finalized_contracts,
      icon: FileCheck,
      color: 'text-emerald-600 bg-emerald-50',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map(({ label, value, icon: Icon, color }) => (
        <div key={label} className="card p-4">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center mb-3 ${color}`}>
            <Icon className="w-5 h-5" />
          </div>
          <div className="text-2xl font-bold text-gray-900">{value}</div>
          <div className="text-sm text-gray-500">{label}</div>
        </div>
      ))}
    </div>
  );
}
