// S2 — Activity timeline: kronolojik liste + Not/Arama/E-posta/Toplantı
// Kaydet. Subject-parametreli, genel amaçlı — hem Müşteri Detay (WB-6) hem
// Offer/Contract ekranları (WB-7) tarafından REUSE edilir (owner kararı:
// kod tekrarından kaçın).
import { useCallback, useEffect, useState } from 'react';
import { Loader2, AlertCircle, MessageSquarePlus, Phone, Mail, Users2 } from 'lucide-react';
import { listActivities, createActivity, ActivityOut, ActivityType, Subject } from './crmApi';
import { formatDateTimeTr } from './dateUtils';

const ACTIVITY_LABELS: Record<string, string> = {
  NOTE: 'Not',
  CALL: 'Arama',
  EMAIL: 'E-posta',
  MEETING: 'Toplantı',
  STATUS_CHANGE: 'Durum değişikliği',
  TASK_COMPLETED: 'Görev tamamlandı',
};

const CREATE_BUTTONS: Array<{ type: Exclude<ActivityType, 'TASK_COMPLETED'>; label: string; icon: typeof MessageSquarePlus }> = [
  { type: 'NOTE', label: 'Not Ekle', icon: MessageSquarePlus },
  { type: 'CALL', label: 'Arama Kaydet', icon: Phone },
  { type: 'EMAIL', label: 'E-posta Kaydet', icon: Mail },
  { type: 'MEETING', label: 'Toplantı Kaydet', icon: Users2 },
];

interface ActivityTimelineProps {
  subject: Subject;
}

export function ActivityTimeline({ subject }: ActivityTimelineProps) {
  const [activities, setActivities] = useState<ActivityOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formType, setFormType] = useState<Exclude<ActivityType, 'TASK_COMPLETED'> | null>(null);
  const [formTitle, setFormTitle] = useState('');
  const [formBody, setFormBody] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const subjectKey = JSON.stringify(subject);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listActivities(subject, { limit: 100 });
      setActivities(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Aktiviteler yüklenemedi.');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectKey]);

  useEffect(() => {
    load();
  }, [load]);

  const openForm = (type: Exclude<ActivityType, 'TASK_COMPLETED'>) => {
    setFormType(type);
    setFormTitle('');
    setFormBody('');
  };

  const handleSave = async () => {
    if (!formType) return;
    setSubmitting(true);
    setError(null);
    try {
      await createActivity(subject, formType, formTitle || undefined, formBody || undefined);
      setFormType(null);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Aktivite kaydedilemedi.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {CREATE_BUTTONS.map(({ type, label, icon: Icon }) => (
          <button key={type} onClick={() => openForm(type)} className="btn-secondary text-sm flex items-center gap-1.5">
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {formType && (
        <div className="card p-4 space-y-2">
          <p className="text-sm font-medium text-gray-700">{ACTIVITY_LABELS[formType]} ekle</p>
          <input
            value={formTitle}
            onChange={(e) => setFormTitle(e.target.value)}
            placeholder="Başlık (opsiyonel)"
            className="input w-full"
          />
          <textarea
            value={formBody}
            onChange={(e) => setFormBody(e.target.value)}
            placeholder="Detay..."
            rows={3}
            className="input w-full"
          />
          <div className="flex gap-2">
            <button onClick={handleSave} disabled={submitting} className="btn-primary text-sm disabled:opacity-50">
              {submitting ? 'Kaydediliyor...' : 'Kaydet'}
            </button>
            <button onClick={() => setFormType(null)} className="btn-secondary text-sm">
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
      ) : activities.length === 0 ? (
        <div className="card p-8 text-center text-gray-500">Henüz aktivite yok.</div>
      ) : (
        <ul className="space-y-2">
          {activities.map((a) => (
            <li key={`${a.source}-${a.id}`} className="card p-3">
              <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                <span className="font-medium">{ACTIVITY_LABELS[a.activity_type] || a.activity_type}</span>
                <span>{formatDateTimeTr(a.occurred_at)}</span>
              </div>
              {a.title && <div className="text-sm font-medium text-gray-900">{a.title}</div>}
              {a.body && <div className="text-sm text-gray-600 mt-0.5">{a.body}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
