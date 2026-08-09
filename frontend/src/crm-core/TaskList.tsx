// S2 — Task listesi: yeni görev (due date/time) + tamamla + iptal.
// Subject-parametreli, genel amaçlı — hem Müşteri Detay (WB-6) hem
// Offer/Contract ekranları (WB-7) tarafından REUSE edilir.
import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertCircle, Plus, Check, X } from 'lucide-react';
import { listTasks, createTask, completeTask, cancelTask, TaskOut, Subject } from './crmApi';
import { formatDateTimeTr } from './dateUtils';

const STATUS_LABELS: Record<string, string> = { OPEN: 'Açık', COMPLETED: 'Tamamlandı', CANCELLED: 'İptal' };
const STATUS_STYLES: Record<string, string> = {
  OPEN: 'bg-blue-100 text-blue-700',
  COMPLETED: 'bg-green-100 text-green-700',
  CANCELLED: 'bg-gray-100 text-gray-500',
};

// Türkiye 2016'dan beri DST uygulamıyor (sabit UTC+3) — datetime-local
// input'un yerel (tarayıcı) değerini bu sabit offset'le UTC'ye çevirmek
// güvenilir. Owner kararı: "Türkiye local-time stringlerini DB'ye rastgele
// yazma" — bu yüzden ham input değeri ASLA doğrudan gönderilmez.
function trLocalInputToUtcIso(localValue: string): string {
  return new Date(`${localValue}:00+03:00`).toISOString();
}

interface TaskListProps {
  subject: Subject;
}

export function TaskList({ subject }: TaskListProps) {
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formTitle, setFormTitle] = useState('');
  const [formDueAt, setFormDueAt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);

  const subjectKey = JSON.stringify(subject);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listTasks({ ...subject, limit: 100 });
      setTasks(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Görevler yüklenemedi.');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectKey]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!formTitle.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createTask(subject, formTitle.trim(), undefined, formDueAt ? trLocalInputToUtcIso(formDueAt) : undefined);
      setFormOpen(false);
      setFormTitle('');
      setFormDueAt('');
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Görev oluşturulamadı.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleComplete = async (taskId: number) => {
    setActingId(taskId);
    try {
      await completeTask(taskId);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Görev tamamlanamadı.');
    } finally {
      setActingId(null);
    }
  };

  const handleCancel = async (taskId: number) => {
    setActingId(taskId);
    try {
      await cancelTask(taskId);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Görev iptal edilemedi.');
    } finally {
      setActingId(null);
    }
  };

  return (
    <div className="space-y-4">
      {!formOpen ? (
        <button onClick={() => setFormOpen(true)} className="btn-secondary text-sm flex items-center gap-1.5">
          <Plus className="w-4 h-4" />
          Yeni Görev
        </button>
      ) : (
        <div className="card p-4 space-y-2">
          <input
            value={formTitle}
            onChange={(e) => setFormTitle(e.target.value)}
            placeholder="Görev başlığı (örn. Cuma ara)"
            className="input w-full"
          />
          <div>
            <label className="text-xs text-gray-500 block mb-1">Son tarih (opsiyonel)</label>
            <input
              type="datetime-local"
              value={formDueAt}
              onChange={(e) => setFormDueAt(e.target.value)}
              className="input w-full"
            />
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={submitting || !formTitle.trim()} className="btn-primary text-sm disabled:opacity-50">
              {submitting ? 'Kaydediliyor...' : 'Oluştur'}
            </button>
            <button onClick={() => { setFormOpen(false); setFormTitle(''); setFormDueAt(''); }} className="btn-secondary text-sm">
              Vazgeç
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {loading ? (
        <div className="p-8 text-center text-gray-500">
          <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
          Yükleniyor...
        </div>
      ) : tasks.length === 0 ? (
        <div className="card p-8 text-center text-gray-500">Henüz görev yok.</div>
      ) : (
        <ul className="space-y-2">
          {tasks.map((t) => (
            <li key={t.id} className="card p-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[t.status]}`}>
                    {STATUS_LABELS[t.status]}
                  </span>
                  <span className="text-sm font-medium text-gray-900 truncate">{t.title}</span>
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {t.due_at ? `Son tarih: ${formatDateTimeTr(t.due_at)}` : 'Son tarih yok'}
                  {t.status === 'COMPLETED' && t.completed_at && ` · Tamamlandı: ${formatDateTimeTr(t.completed_at)}`}
                </div>
              </div>
              {t.status === 'OPEN' && (
                <div className="flex gap-1.5 flex-shrink-0">
                  <button
                    onClick={() => handleComplete(t.id)}
                    disabled={actingId === t.id}
                    className="btn-secondary text-xs px-2 py-1 flex items-center gap-1 disabled:opacity-50"
                    title="Tamamla"
                  >
                    <Check className="w-3 h-3" /> Tamamla
                  </button>
                  <button
                    onClick={() => handleCancel(t.id)}
                    disabled={actingId === t.id}
                    className="btn-secondary text-xs px-2 py-1 flex items-center gap-1 disabled:opacity-50"
                    title="İptal"
                  >
                    <X className="w-3 h-3" /> İptal
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
