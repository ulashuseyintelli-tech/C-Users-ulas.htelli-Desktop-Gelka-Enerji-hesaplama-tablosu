// S2 WB-7 — Bugün: gerçek veri (S1 placeholder'ının yerini alır). Ayrı bir
// "today" tablosu yok — GET /crm/today her çağrıda mevcut Task/Activity/
// S1 verisinden türetilir (owner kararı). Fake data YOK.
import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertCircle, Users, FileText, CheckCircle2, FileCheck, Check, ExternalLink } from 'lucide-react';
import { getToday, completeTask, TodayResponse, TaskOut } from './crmApi';
import type { Subject } from './crmApi';
import { formatDateTimeTr } from './dateUtils';

function subjectOf(t: { customer_id: number | null; offer_id: number | null; contract_id: number | null }): Subject | null {
  if (t.customer_id !== null) return { customer_id: t.customer_id };
  if (t.offer_id !== null) return { offer_id: t.offer_id };
  if (t.contract_id !== null) return { contract_id: t.contract_id };
  return null;
}

interface TodayScreenProps {
  onOpenSubject: (subject: Subject) => void;
}

export function TodayScreen({ onOpenSubject }: TodayScreenProps) {
  const [data, setData] = useState<TodayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [completingId, setCompletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getToday();
      setData(result);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Bugün özeti yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleComplete = async (taskId: number) => {
    setCompletingId(taskId);
    try {
      await completeTask(taskId);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Görev tamamlanamadı.');
    } finally {
      setCompletingId(null);
    }
  };

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
        Yükleniyor...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
        <p className="text-sm text-red-700">{error || 'Özet yüklenemedi.'}</p>
      </div>
    );
  }

  const cards = [
    { label: 'Toplam Müşteri', value: data.total_customers, icon: Users, color: 'text-blue-600 bg-blue-50' },
    { label: 'Açık Teklifler', value: data.total_open_offers, icon: FileText, color: 'text-amber-600 bg-amber-50' },
    { label: 'Kabul Edilen Teklifler', value: data.total_accepted_offers, icon: CheckCircle2, color: 'text-green-600 bg-green-50' },
    { label: 'Tamamlanmış Sözleşmeler', value: data.total_finalized_contracts, icon: FileCheck, color: 'text-emerald-600 bg-emerald-50' },
  ];

  const renderTaskRow = (t: TaskOut) => {
    const subject = subjectOf(t);
    return (
      <li key={t.id} className="card p-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 truncate">{t.title}</div>
          <div className="text-xs text-gray-500">{t.due_at ? formatDateTimeTr(t.due_at) : 'Son tarih yok'}</div>
        </div>
        <div className="flex gap-1.5 flex-shrink-0">
          <button
            onClick={() => handleComplete(t.id)}
            disabled={completingId === t.id}
            className="btn-secondary text-xs px-2 py-1 flex items-center gap-1 disabled:opacity-50"
            title="Tamamlandı"
          >
            <Check className="w-3 h-3" /> Tamamlandı
          </button>
          {subject && (
            <button
              onClick={() => onOpenSubject(subject)}
              className="btn-secondary text-xs px-2 py-1 flex items-center gap-1"
              title="Kaydı aç"
            >
              <ExternalLink className="w-3 h-3" /> Kaydı Aç
            </button>
          )}
        </div>
      </li>
    );
  };

  return (
    <div className="space-y-6">
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

      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Bugünkü Görevler ({data.due_today_count})</h3>
        {data.due_today_tasks.length === 0 ? (
          <div className="card p-6 text-center text-gray-500 text-sm">Bugün için görev yok.</div>
        ) : (
          <ul className="space-y-2">{data.due_today_tasks.map(renderTaskRow)}</ul>
        )}
      </div>

      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Gecikmiş Görevler ({data.overdue_count})</h3>
        {data.overdue_tasks.length === 0 ? (
          <div className="card p-6 text-center text-gray-500 text-sm">Gecikmiş görev yok.</div>
        ) : (
          <ul className="space-y-2">{data.overdue_tasks.map(renderTaskRow)}</ul>
        )}
      </div>

      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Son Aktiviteler</h3>
        {data.recent_activities.length === 0 ? (
          <div className="card p-6 text-center text-gray-500 text-sm">Henüz aktivite yok.</div>
        ) : (
          <ul className="space-y-2">
            {data.recent_activities.map((a) => {
              const subject = subjectOf(a);
              return (
                <li key={`${a.source}-${a.id}`} className="card p-3 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-gray-900 truncate">{a.title || a.activity_type}</div>
                    <div className="text-xs text-gray-500">{formatDateTimeTr(a.occurred_at)}</div>
                  </div>
                  {subject && (
                    <button
                      onClick={() => onOpenSubject(subject)}
                      className="btn-secondary text-xs px-2 py-1 flex items-center gap-1 flex-shrink-0"
                    >
                      <ExternalLink className="w-3 h-3" /> Kaydı Aç
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
