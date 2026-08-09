// =============================================================================
// S3 — Sales Pipeline — tek bir Kanban kartı.
// =============================================================================
//
// Owner kararı (S3 GO madde 11): kart minimum Firma/Teklif tarihi/Teklif
// tutarı/Katsayı/gerçek offer status badge/Son aktivite/Sonraki görev/
// Gecikmiş görev badge/Contract indicator içerir — "bilgi çöplüğü" olmasın.
//
// Drag/drop: native HTML5 DnD (ek kütüphane yok — proje zaten hiçbir
// dnd paketi kullanmıyor). Yalnız card.allowed_transitions'taki bir hedef
// status'e drag edilebilir (owner madde 8) — frontend VALID_OFFER_TRANSITIONS
// KOPYALAMAZ, bu bilgi backend'den (allowed_transitions alanı) gelir.
//
// STATUS_LABELS / ACTIVITY_LABELS burada AYRICA (küçük) tanımlanır —
// OffersScreen.tsx/ActivityTimeline.tsx'teki (S1/S2) eşdeğerleri export
// edilmemiş ve o dosyalara dokunulmuyor (owner: "S1/S2'ye artık dokunmayın").
import { Phone, Mail, Users2, MessageSquarePlus, RefreshCw, Clock, FileCheck } from 'lucide-react';
import type { PipelineCardOut } from './crmApi';
import { formatDateTr, formatDateTimeTr } from './dateUtils';

const OFFER_STATUS_LABELS: Record<string, string> = {
  draft: 'Taslak', sent: 'Gönderildi', viewed: 'Görüntülendi', accepted: 'Kabul edildi',
  contracting: 'Sözleşme sürecinde', completed: 'Tamamlandı', rejected: 'Reddedildi', expired: 'Süresi doldu',
};

const ACTIVITY_ICONS: Record<string, typeof Phone> = {
  CALL: Phone, EMAIL: Mail, MEETING: Users2, NOTE: MessageSquarePlus,
  TASK_COMPLETED: FileCheck, STATUS_CHANGE: RefreshCw,
};

const ACTIVITY_LABELS: Record<string, string> = {
  CALL: 'Arama', EMAIL: 'E-posta', MEETING: 'Toplantı', NOTE: 'Not',
  TASK_COMPLETED: 'Görev tamamlandı', STATUS_CHANGE: 'Durum değişikliği',
};

const WARNING_LABELS: Record<string, string> = {
  CONTRACT_STATUS_WITHOUT_CONTRACT: 'Sözleşme sürecinde ama sözleşme kaydı yok',
  COMPLETED_WITHOUT_CONTRACT: 'Tamamlandı ama sözleşme kaydı yok',
  MISSING_CUSTOMER: 'Müşteri atanmamış',
  UNKNOWN_OFFER_STATUS: 'Bilinmeyen teklif durumu',
};

interface PipelineCardProps {
  card: PipelineCardOut;
  draggable: boolean;
  onDragStart: (offerId: number) => void;
  onDragEnd: () => void;
  onOpenOffer: (offerId: number) => void;
  onOpenContract?: (contractId: number) => void;
}

export function PipelineCard({ card, draggable, onDragStart, onDragEnd, onOpenOffer, onOpenContract }: PipelineCardProps) {
  const ActivityIcon = card.last_activity ? ACTIVITY_ICONS[card.last_activity.activity_type] : null;

  return (
    <div
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move';
        onDragStart(card.offer_id);
      }}
      onDragEnd={onDragEnd}
      className={`card p-3 space-y-2 ${draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          onClick={() => onOpenOffer(card.offer_id)}
          className="text-sm font-medium text-gray-900 hover:text-primary-600 text-left truncate"
          title={card.customer_name || 'Müşterisiz'}
        >
          {card.customer_name || 'Müşterisiz'}
        </button>
        {card.overdue_task_count > 0 && (
          <span className="inline-flex items-center gap-0.5 rounded-full bg-red-100 text-red-700 text-xs px-1.5 py-0.5 flex-shrink-0" title={`${card.overdue_task_count} gecikmiş görev`}>
            <Clock className="w-3 h-3" /> {card.overdue_task_count}
          </span>
        )}
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{formatDateTr(card.offer_date)}</span>
        <span className="font-medium text-gray-700">{card.offer_total.toLocaleString('tr-TR', { maximumFractionDigits: 0 })} TL</span>
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>Katsayı: {card.agreement_multiplier.toFixed(2)}</span>
        <span className="inline-flex items-center rounded-full bg-gray-100 text-gray-600 px-1.5 py-0.5">
          {OFFER_STATUS_LABELS[card.offer_status] || card.offer_status}
        </span>
      </div>

      {card.pipeline_warning && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-1">
          ⚠ {WARNING_LABELS[card.pipeline_warning] || card.pipeline_warning}
        </div>
      )}

      {card.has_contract && card.contract_id && (
        <button
          onClick={() => onOpenContract?.(card.contract_id!)}
          className="flex items-center gap-1 text-xs text-primary-600 hover:underline"
        >
          <FileCheck className="w-3 h-3" />
          Sözleşme {card.contract_status ? `(${card.contract_status})` : ''}
        </button>
      )}

      {card.last_activity && (
        <div className="flex items-center gap-1 text-xs text-gray-500 border-t border-gray-100 pt-1.5">
          {ActivityIcon && <ActivityIcon className="w-3 h-3" />}
          <span>Son aktivite: {ACTIVITY_LABELS[card.last_activity.activity_type] || card.last_activity.activity_type}</span>
          <span className="text-gray-400">· {formatDateTimeTr(card.last_activity.occurred_at)}</span>
        </div>
      )}

      {card.next_open_task && (
        <div className="text-xs text-gray-500">
          Sonraki: {card.next_open_task.title}
          {card.next_open_task.due_at && <span className="text-gray-400"> · {formatDateTimeTr(card.next_open_task.due_at)}</span>}
        </div>
      )}
    </div>
  );
}
