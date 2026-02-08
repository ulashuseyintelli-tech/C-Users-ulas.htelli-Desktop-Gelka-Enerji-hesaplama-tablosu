import React from 'react';
import type { MarketPriceRecord, PaginationState } from './types';
import { PAGE_SIZE_OPTIONS } from './constants';
import { formatPrice, formatDateTime } from './utils';
import { StatusBadge } from './StatusBadge';
import { SkeletonLoader } from './SkeletonLoader';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PriceListTableProps {
  data: MarketPriceRecord[];
  loading: boolean;
  pagination: PaginationState;
  sortBy: string;
  sortOrder: 'asc' | 'desc';
  onSort: (column: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onEdit: (record: MarketPriceRecord) => void;
  onHistory?: (record: MarketPriceRecord) => void;
  onClearFilters: () => void;
  isEmpty: boolean;
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

interface ColumnDef {
  key: string;
  label: string;
  sortable: boolean;
}

const COLUMNS: ColumnDef[] = [
  { key: 'period', label: 'Dönem', sortable: true },
  { key: 'ptf_tl_per_mwh', label: 'PTF (TL/MWh)', sortable: true },
  { key: 'status', label: 'Durum', sortable: true },
  { key: 'updated_at', label: 'Güncelleme', sortable: true },
  { key: 'source', label: 'Kaynak', sortable: false },
  { key: 'updated_by', label: 'Güncelleyen', sortable: false },
  { key: 'change_reason', label: 'Değişiklik Nedeni', sortable: false },
  { key: 'action', label: 'İşlem', sortable: false },
];

const MAX_REASON_LENGTH = 30;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength) + '…';
}

function totalPages(pagination: PaginationState): number {
  if (pagination.total <= 0) return 1;
  return Math.ceil(pagination.total / pagination.pageSize);
}

// ---------------------------------------------------------------------------
// Sort indicator
// ---------------------------------------------------------------------------

function SortIndicator({ column, sortBy, sortOrder }: { column: string; sortBy: string; sortOrder: 'asc' | 'desc' }) {
  if (column !== sortBy) {
    return <span className="ml-1 text-gray-300">⇅</span>;
  }
  return (
    <span className="ml-1" data-testid={`sort-indicator-${column}`}>
      {sortOrder === 'asc' ? '▲' : '▼'}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ onClearFilters }: { onClearFilters: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-500" data-testid="empty-state">
      <svg className="w-16 h-16 mb-4 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-2.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
      </svg>
      <p className="text-lg font-medium mb-2">Kayıt bulunamadı</p>
      <p className="text-sm mb-4">Mevcut filtrelerle eşleşen kayıt yok.</p>
      <button
        type="button"
        onClick={onClearFilters}
        className="inline-flex items-center rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
      >
        Filtreleri Temizle
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pagination controls
// ---------------------------------------------------------------------------

function PaginationControls({
  pagination,
  onPageChange,
  onPageSizeChange,
}: {
  pagination: PaginationState;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  const total = totalPages(pagination);
  const { page } = pagination;

  return (
    <div className="flex items-center justify-between border-t border-gray-200 bg-white px-4 py-3" data-testid="pagination-controls">
      {/* Page size selector */}
      <div className="flex items-center gap-2">
        <label htmlFor="page-size-select" className="text-sm text-gray-700">
          Sayfa başına:
        </label>
        <select
          id="page-size-select"
          value={pagination.pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="rounded border-gray-300 text-sm focus:border-blue-500 focus:ring-blue-500"
          aria-label="Sayfa boyutu"
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </div>

      {/* Page info and navigation */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-700" data-testid="page-info">
          Sayfa {page} / {total}
        </span>

        <nav className="inline-flex -space-x-px rounded-md shadow-sm" aria-label="Sayfalama">
          {/* First page */}
          <button
            type="button"
            onClick={() => onPageChange(1)}
            disabled={page <= 1}
            className="relative inline-flex items-center rounded-l-md px-2 py-2 text-gray-400 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="İlk sayfa"
          >
            «
          </button>

          {/* Previous page */}
          <button
            type="button"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="relative inline-flex items-center px-2 py-2 text-gray-400 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Önceki sayfa"
          >
            ‹
          </button>

          {/* Next page */}
          <button
            type="button"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= total}
            className="relative inline-flex items-center px-2 py-2 text-gray-400 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Sonraki sayfa"
          >
            ›
          </button>

          {/* Last page */}
          <button
            type="button"
            onClick={() => onPageChange(total)}
            disabled={page >= total}
            className="relative inline-flex items-center rounded-r-md px-2 py-2 text-gray-400 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Son sayfa"
          >
            »
          </button>
        </nav>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cell renderer
// ---------------------------------------------------------------------------

function renderCell(
  record: MarketPriceRecord,
  columnKey: string,
  onEdit: (r: MarketPriceRecord) => void,
  onHistory?: (r: MarketPriceRecord) => void,
): React.ReactNode {
  switch (columnKey) {
    case 'period':
      return record.period;
    case 'ptf_tl_per_mwh':
      return formatPrice(record.ptf_tl_per_mwh);
    case 'status':
      return <StatusBadge status={record.status} />;
    case 'updated_at':
      return formatDateTime(record.updated_at);
    case 'source':
      return record.source;
    case 'updated_by':
      return record.updated_by;
    case 'change_reason':
      return record.change_reason
        ? <span title={record.change_reason}>{truncateText(record.change_reason, MAX_REASON_LENGTH)}</span>
        : <span className="text-gray-400">—</span>;
    case 'action':
      return (
        <span className="inline-flex gap-1">
          <button
            type="button"
            onClick={() => onEdit(record)}
            className="inline-flex items-center rounded px-2.5 py-1.5 text-sm font-medium text-blue-600 hover:text-blue-800 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label={`${record.period} düzenle`}
          >
            Düzenle
          </button>
          {onHistory && (
            <button
              type="button"
              onClick={() => onHistory(record)}
              className="inline-flex items-center rounded px-2.5 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-400"
              aria-label={`${record.period} geçmiş`}
            >
              Geçmiş
            </button>
          )}
        </span>
      );
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const PriceListTable: React.FC<PriceListTableProps> = ({
  data,
  loading,
  pagination,
  sortBy,
  sortOrder,
  onSort,
  onPageChange,
  onPageSizeChange,
  onEdit,
  onHistory,
  onClearFilters,
  isEmpty,
}) => {
  // Loading state → skeleton
  if (loading) {
    return <SkeletonLoader />;
  }

  // Empty state
  if (isEmpty && data.length === 0) {
    return <EmptyState onClearFilters={onClearFilters} />;
  }

  return (
    <div>
      {/* Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  className={`px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 ${
                    col.sortable ? 'cursor-pointer select-none hover:bg-gray-100' : ''
                  }`}
                  onClick={col.sortable ? () => onSort(col.key) : undefined}
                  aria-sort={
                    col.sortable && col.key === sortBy
                      ? sortOrder === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : undefined
                  }
                >
                  <span className="inline-flex items-center">
                    {col.label}
                    {col.sortable && (
                      <SortIndicator column={col.key} sortBy={sortBy} sortOrder={sortOrder} />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-white">
            {data.map((record) => (
              <tr key={`${record.period}-${record.price_type}`} className="hover:bg-gray-50">
                {COLUMNS.map((col) => (
                  <td key={col.key} className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                    {renderCell(record, col.key, onEdit, onHistory)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <PaginationControls
        pagination={pagination}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </div>
  );
};
