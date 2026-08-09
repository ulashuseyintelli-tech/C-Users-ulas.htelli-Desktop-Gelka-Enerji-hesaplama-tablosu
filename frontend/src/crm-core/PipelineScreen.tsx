// =============================================================================
// S3 — Sales Pipeline — Kanban shell.
// =============================================================================
//
// Owner kararları (S3 GO):
// - Yeni domain model YOK (madde 1) — bu ekran salt PROJECTION render eder.
// - 6 kolon: DRAFT/SENT/ACCEPTED/CONTRACT/COMPLETED/LOST (madde 2).
// - Serbest drag/drop YOK — yalnız card.allowed_transitions'a göre geçerli
//   hedef kolonlara drop aktif olur; her mutation mevcut
//   PUT /offers/{id}/status (updateOfferStatus, frontend/src/api.ts) ile
//   yapılır (madde 8).
// - CONTRACT/COMPLETED kolonları MANUEL atanamaz — Contract lifecycle
//   sonucudur, drop target değildir (madde 9).
// - Kart tıklaması yeni bir Offer detay ekranı AÇMAZ — mevcut onOpenSubject
//   mekanizması (CrmCorePage.tsx) reuse edilir (madde 10).
// - React Router eklenmedi (CrmCorePage'in mevcut state-tabanlı deseni).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, AlertCircle, Search, X } from 'lucide-react';
import { getPipeline, PipelineCardOut, PipelineResponse, PipelineStage } from './crmApi';
import { updateOfferStatus } from '../api';
import { PipelineCard } from './PipelineCard';
import type { Subject } from './crmApi';

// Sıralama + Türkçe etiketler (owner madde 2). LOST en sonda — terminal
// durum, doğal okuma akışında son sütun.
const STAGE_ORDER: PipelineStage[] = ['DRAFT', 'SENT', 'ACCEPTED', 'CONTRACT', 'COMPLETED', 'LOST'];
const STAGE_LABELS: Record<PipelineStage, string> = {
  DRAFT: 'Taslak', SENT: 'Gönderildi', ACCEPTED: 'Kabul', CONTRACT: 'Sözleşme', COMPLETED: 'Tamamlandı', LOST: 'Kaybedildi',
};

// Bir kart bir kolona sürüklenirse hangi Offer.status hedeflenir — owner
// madde 4 (manuel Kaybedildi = rejected, expired sistemsel) + madde 9
// (CONTRACT/COMPLETED drop target DEĞİL, null = drop devre dışı).
function targetOfferStatusForStage(stage: PipelineStage): string | null {
  switch (stage) {
    case 'DRAFT': return 'draft';
    case 'SENT': return 'sent';
    case 'ACCEPTED': return 'accepted';
    case 'LOST': return 'rejected';
    case 'CONTRACT':
    case 'COMPLETED':
      return null;
  }
}

const OFFER_STATUS_OPTIONS = [
  { value: '', label: 'Tüm durumlar' },
  { value: 'draft', label: 'Taslak' },
  { value: 'sent', label: 'Gönderildi' },
  { value: 'viewed', label: 'Görüntülendi' },
  { value: 'accepted', label: 'Kabul edildi' },
  { value: 'contracting', label: 'Sözleşme sürecinde' },
  { value: 'completed', label: 'Tamamlandı' },
  { value: 'rejected', label: 'Reddedildi' },
  { value: 'expired', label: 'Süresi doldu' },
];

interface PipelineScreenProps {
  onOpenSubject: (subject: Subject) => void;
}

export function PipelineScreen({ onOpenSubject }: PipelineScreenProps) {
  const [data, setData] = useState<PipelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filtreler (owner madde 12) — assignee filtresi YOK (SINGLE OPERATOR).
  const [customerSearch, setCustomerSearch] = useState('');
  const [stageFilter, setStageFilter] = useState<PipelineStage | ''>('');
  const [statusFilter, setStatusFilter] = useState('');
  const [hasContractFilter, setHasContractFilter] = useState(false);
  const [overdueFilter, setOverdueFilter] = useState(false);

  const [draggingOfferId, setDraggingOfferId] = useState<number | null>(null);
  const [dragOverStage, setDragOverStage] = useState<PipelineStage | null>(null);
  const [transitioning, setTransitioning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getPipeline({
        customer_search: customerSearch || undefined,
        stage: stageFilter || undefined,
        offer_status: statusFilter || undefined,
        has_contract: hasContractFilter || undefined,
        overdue_only: overdueFilter || undefined,
        limit: 200,
      });
      setData(result);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Pipeline yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, [customerSearch, stageFilter, statusFilter, hasContractFilter, overdueFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const cardsByStage = useMemo(() => {
    const grouped: Record<PipelineStage, PipelineCardOut[]> = {
      DRAFT: [], SENT: [], ACCEPTED: [], CONTRACT: [], COMPLETED: [], LOST: [],
    };
    for (const card of data?.cards || []) {
      grouped[card.pipeline_stage].push(card);
    }
    return grouped;
  }, [data]);

  const draggingCard = useMemo(
    () => data?.cards.find((c) => c.offer_id === draggingOfferId) || null,
    [data, draggingOfferId]
  );

  const handleDrop = async (targetStage: PipelineStage) => {
    setDragOverStage(null);
    if (!draggingCard) return;
    const targetStatus = targetOfferStatusForStage(targetStage);
    if (!targetStatus) return; // CONTRACT/COMPLETED — drop target değil
    if (!draggingCard.allowed_transitions.includes(targetStatus)) return; // geçersiz transition — sessizce reddet

    setTransitioning(true);
    setError(null);
    try {
      await updateOfferStatus(draggingCard.offer_id, targetStatus);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Durum güncellenemedi.');
    } finally {
      setTransitioning(false);
      setDraggingOfferId(null);
    }
  };

  const isDropTargetActive = (stage: PipelineStage): boolean => {
    if (!draggingCard) return false;
    const targetStatus = targetOfferStatusForStage(stage);
    if (!targetStatus) return false;
    return draggingCard.allowed_transitions.includes(targetStatus);
  };

  return (
    <div className="space-y-4">
      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap gap-3 items-center">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={customerSearch}
              onChange={(e) => setCustomerSearch(e.target.value)}
              placeholder="Firma ara..."
              className="input w-full pl-9"
            />
          </div>
          <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value as PipelineStage | '')} className="input">
            <option value="">Tüm kolonlar</option>
            {STAGE_ORDER.map((s) => (
              <option key={s} value={s}>{STAGE_LABELS[s]}</option>
            ))}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="input">
            {OFFER_STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input type="checkbox" checked={hasContractFilter} onChange={(e) => setHasContractFilter(e.target.checked)} />
            Sözleşmesi olanlar
          </label>
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input type="checkbox" checked={overdueFilter} onChange={(e) => setOverdueFilter(e.target.checked)} />
            Gecikmiş görevi olanlar
          </label>
          {(customerSearch || stageFilter || statusFilter || hasContractFilter || overdueFilter) && (
            <button
              onClick={() => { setCustomerSearch(''); setStageFilter(''); setStatusFilter(''); setHasContractFilter(false); setOverdueFilter(false); }}
              className="btn-secondary text-xs flex items-center gap-1"
            >
              <X className="w-3 h-3" /> Temizle
            </button>
          )}
        </div>
      </div>

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
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3 items-start">
          {STAGE_ORDER.map((stage) => {
            const cards = cardsByStage[stage];
            const dropActive = dragOverStage === stage && isDropTargetActive(stage);
            const dropPossible = draggingCard !== null && isDropTargetActive(stage);
            return (
              <div
                key={stage}
                onDragOver={(e) => {
                  if (dropPossible) {
                    e.preventDefault();
                    setDragOverStage(stage);
                  }
                }}
                onDragLeave={() => setDragOverStage((s) => (s === stage ? null : s))}
                onDrop={(e) => {
                  e.preventDefault();
                  handleDrop(stage);
                }}
                className={`rounded-lg p-2 min-h-[120px] space-y-2 transition-colors ${
                  dropActive ? 'bg-primary-50 ring-2 ring-primary-400' : draggingCard && !dropPossible ? 'bg-gray-50 opacity-60' : 'bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between px-1">
                  <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">{STAGE_LABELS[stage]}</h3>
                  <span className="text-xs text-gray-400">{cards.length}</span>
                </div>
                {cards.length === 0 ? (
                  <div className="text-xs text-gray-400 text-center py-4">—</div>
                ) : (
                  cards.map((card) => (
                    <PipelineCard
                      key={card.offer_id}
                      card={card}
                      // Sürüklenebilirlik yalnız KARTIN KENDİ allowed_transitions'ına
                      // bağlı — bulunduğu kolona (stage) değil. Örn. CONTRACT
                      // kolonundaki "contracting" bir offer LOST'a (rejected)
                      // sürüklenebilir olmalı; offer_lifecycle zaten izin veriyor.
                      draggable={card.allowed_transitions.length > 0 && !transitioning}
                      onDragStart={setDraggingOfferId}
                      onDragEnd={() => { setDraggingOfferId(null); setDragOverStage(null); }}
                      onOpenOffer={() => onOpenSubject({ offer_id: card.offer_id })}
                      onOpenContract={card.contract_id ? () => onOpenSubject({ contract_id: card.contract_id! }) : undefined}
                    />
                  ))
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
