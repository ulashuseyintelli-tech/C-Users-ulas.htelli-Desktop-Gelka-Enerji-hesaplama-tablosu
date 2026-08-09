// S2 WB-7 — Offer/Contract liste satırlarında kompakt "Not Ekle" +
// "Takip Görevi Oluştur" aksiyonu (owner talimatı: offer lifecycle veya
// contract generation/finalize akışına DOKUNULMADI, yalnız yeni bağımsız
// bir aksiyon eklendi).
import { useState } from 'react';
import { StickyNote, ListPlus, Check } from 'lucide-react';
import { createActivity, createTask, Subject } from './crmApi';

interface QuickCrmActionsProps {
  subject: Subject;
}

export function QuickCrmActions({ subject }: QuickCrmActionsProps) {
  const [mode, setMode] = useState<'none' | 'note' | 'task'>('none');
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setMode('none');
    setText('');
    setError(null);
  };

  const handleSave = async () => {
    if (!text.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      if (mode === 'note') {
        await createActivity(subject, 'NOTE', undefined, text.trim());
      } else if (mode === 'task') {
        await createTask(subject, text.trim());
      }
      reset();
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Kaydedilemedi.');
    } finally {
      setSubmitting(false);
    }
  };

  if (mode === 'none') {
    return (
      <div className="flex items-center gap-1.5">
        <button onClick={() => setMode('note')} className="btn-secondary text-xs px-2 py-1 flex items-center gap-1" title="Not Ekle">
          <StickyNote className="w-3 h-3" /> Not Ekle
        </button>
        <button onClick={() => setMode('task')} className="btn-secondary text-xs px-2 py-1 flex items-center gap-1" title="Takip Görevi Oluştur">
          <ListPlus className="w-3 h-3" /> Takip Görevi
        </button>
        {justSaved && (
          <span className="text-xs text-green-600 flex items-center gap-0.5">
            <Check className="w-3 h-3" /> Kaydedildi
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={mode === 'note' ? 'Not...' : 'Görev başlığı...'}
          className="input text-xs py-1 px-2 w-40"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSave();
            if (e.key === 'Escape') reset();
          }}
        />
        <button onClick={handleSave} disabled={submitting || !text.trim()} className="btn-primary text-xs px-2 py-1 disabled:opacity-50">
          {submitting ? '...' : 'Kaydet'}
        </button>
        <button onClick={reset} className="btn-secondary text-xs px-2 py-1">
          Vazgeç
        </button>
      </div>
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
