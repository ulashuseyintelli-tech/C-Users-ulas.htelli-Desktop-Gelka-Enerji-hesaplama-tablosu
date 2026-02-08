// =============================================================================
// Audit History — HistoryPanel (Modal)
// =============================================================================
// Read-only modal displaying change history for a market price record.
// States: loading, data, empty, error (with retry).
// All labels Turkish.
//
// Feature: audit-history, Requirements: 4.1-4.5
// =============================================================================

import React from 'react';
import { useAuditHistory } from './hooks/useAuditHistory';
import type { AuditHistoryEntry } from './types';

interface HistoryPanelProps {
  open: boolean;
  period: string | null;
  priceType?: string;
  onClose: () => void;
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('tr-TR', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatValue(val: number | null): string {
  if (val === null || val === undefined) return '—';
  return val.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function ActionBadge({ action }: { action: string }) {
  const isInsert = action === 'INSERT';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        isInsert
          ? 'bg-green-100 text-green-700'
          : 'bg-blue-100 text-blue-700'
      }`}
    >
      {isInsert ? 'Oluşturma' : 'Güncelleme'}
    </span>
  );
}

function StatusText({ status }: { status: string | null }) {
  if (!status) return <span className="text-gray-400">—</span>;
  return (
    <span className={status === 'final' ? 'text-green-700 font-medium' : 'text-yellow-600'}>
      {status === 'final' ? 'Kesinleşmiş' : 'Geçici'}
    </span>
  );
}

function HistoryRow({ entry }: { entry: AuditHistoryEntry }) {
  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50">
      <td className="px-3 py-2 text-sm whitespace-nowrap">{formatDateTime(entry.created_at)}</td>
      <td className="px-3 py-2 text-sm"><ActionBadge action={entry.action} /></td>
      <td className="px-3 py-2 text-sm text-right tabular-nums">
        {entry.action === 'UPDATE' ? (
          <span>
            <span className="text-gray-400">{formatValue(entry.old_value)}</span>
            <span className="mx-1 text-gray-300">→</span>
            <span className="font-medium">{formatValue(entry.new_value)}</span>
          </span>
        ) : (
          <span className="font-medium">{formatValue(entry.new_value)}</span>
        )}
      </td>
      <td className="px-3 py-2 text-sm">
        {entry.action === 'UPDATE' ? (
          <span>
            <StatusText status={entry.old_status} />
            <span className="mx-1 text-gray-300">→</span>
            <StatusText status={entry.new_status} />
          </span>
        ) : (
          <StatusText status={entry.new_status} />
        )}
      </td>
      <td className="px-3 py-2 text-sm text-gray-600 max-w-[200px] truncate" title={entry.change_reason || ''}>
        {entry.change_reason || <span className="text-gray-400">—</span>}
      </td>
      <td className="px-3 py-2 text-sm text-gray-600">{entry.updated_by || '—'}</td>
      <td className="px-3 py-2 text-sm text-gray-500">{entry.source || '—'}</td>
    </tr>
  );
}

function SkeletonRows() {
  return (
    <>
      {[1, 2, 3].map((i) => (
        <tr key={i} className="border-b border-gray-100 animate-pulse">
          {[1, 2, 3, 4, 5, 6, 7].map((j) => (
            <td key={j} className="px-3 py-2">
              <div className="h-4 bg-gray-200 rounded w-full" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export const HistoryPanel: React.FC<HistoryPanelProps> = ({
  open, period, priceType = 'PTF', onClose,
}) => {
  const { history, loading, error, refetch } = useAuditHistory(
    open ? period : null,
    priceType,
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={`${period} değişiklik geçmişi`}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[80vh] flex flex-col mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h3 className="text-lg font-semibold text-gray-900">
            Değişiklik Geçmişi — {period}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Kapat"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {error ? (
            <div className="text-center py-8">
              <p className="text-red-600 mb-3">Geçmiş yüklenemedi</p>
              <button
                type="button"
                onClick={refetch}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Tekrar Dene
              </button>
            </div>
          ) : !loading && history.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              Bu kayıt için değişiklik geçmişi bulunmamaktadır
            </div>
          ) : (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b-2 border-gray-200">
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Tarih</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">İşlem</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase text-right">Değer (TL/MWh)</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Durum</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Neden</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Güncelleyen</th>
                  <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Kaynak</th>
                </tr>
              </thead>
              <tbody>
                {loading ? <SkeletonRows /> : history.map((entry) => (
                  <HistoryRow key={entry.id} entry={entry} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};
