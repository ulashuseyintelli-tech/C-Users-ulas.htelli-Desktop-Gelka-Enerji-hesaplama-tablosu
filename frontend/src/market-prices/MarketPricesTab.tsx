import React, { useState, useCallback, useMemo } from 'react';
import { useUrlState } from './hooks/useUrlState';
import { useMarketPricesList } from './hooks/useMarketPricesList';
import { PriceFilters } from './PriceFilters';
import { PriceListTable } from './PriceListTable';
import { UpsertFormModal } from './UpsertFormModal';
import { BulkImportWizard } from './BulkImportWizard';
import { ToastNotification } from './ToastNotification';
import { HistoryPanel } from './HistoryPanel';
import type { MarketPriceRecord, ToastMessage, ListParams, FilterState } from './types';

// =============================================================================
// MarketPricesTab — Orchestrator Component
// =============================================================================
// Composes all market-prices sub-components and wires hooks together.
// Manages: URL state, list fetching, modal state, toast state.
// Requirements: 10.4, 10.5, 3.5, 11.5
// =============================================================================

export const MarketPricesTab: React.FC = () => {
  const {
    filters,
    pagination: urlPagination,
    sortBy,
    sortOrder,
    setFilters,
    setPage,
    setPageSize,
    setSort,
    clearFilters,
  } = useUrlState();

  // Build ListParams from URL state
  const listParams: ListParams = useMemo(() => ({
    page: urlPagination.page,
    page_size: urlPagination.pageSize,
    sort_by: sortBy,
    sort_order: sortOrder,
    price_type: 'PTF',
    ...(filters.status !== 'all' ? { status: filters.status as 'provisional' | 'final' } : {}),
    ...(filters.fromPeriod ? { from_period: filters.fromPeriod } : {}),
    ...(filters.toPeriod ? { to_period: filters.toPeriod } : {}),
  }), [urlPagination, sortBy, sortOrder, filters]);

  const { data, pagination, loading, refetch } = useMarketPricesList(listParams);

  // ---- Modal state ----
  const [upsertModal, setUpsertModal] = useState<{
    open: boolean;
    editingRecord?: MarketPriceRecord;
  }>({ open: false });

  const [bulkImportOpen, setBulkImportOpen] = useState(false);

  // ---- History panel state ----
  const [historyModal, setHistoryModal] = useState<{
    open: boolean;
    period: string | null;
  }>({ open: false, period: null });

  // ---- Toast state ----
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const addToast = useCallback((toast: ToastMessage) => {
    setToasts((prev) => [...prev, toast]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ---- Handlers ----
  const handleEdit = useCallback((record: MarketPriceRecord) => {
    setUpsertModal({ open: true, editingRecord: record });
  }, []);

  const handleHistory = useCallback((record: MarketPriceRecord) => {
    setHistoryModal({ open: true, period: record.period });
  }, []);

  const handleNewRecord = useCallback(() => {
    setUpsertModal({ open: true, editingRecord: undefined });
  }, []);

  const handleUpsertClose = useCallback(() => {
    setUpsertModal({ open: false });
  }, []);

  const handleFilterChange = useCallback((f: Partial<FilterState>) => {
    setFilters(f);
  }, [setFilters]);

  const isEmpty = !loading && data.length === 0;

  return (
    <div className="space-y-4">
      {/* Header with action buttons */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-xl font-semibold text-gray-900">Piyasa Fiyatları</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setBulkImportOpen(true)}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Toplu Import
          </button>
          <button
            type="button"
            onClick={handleNewRecord}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Yeni Kayıt
          </button>
        </div>
      </div>

      {/* Filters */}
      <PriceFilters filters={filters} onFilterChange={handleFilterChange} />

      {/* Table */}
      <PriceListTable
        data={data}
        loading={loading}
        pagination={pagination}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={setSort}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
        onEdit={handleEdit}
        onHistory={handleHistory}
        onClearFilters={clearFilters}
        isEmpty={isEmpty}
      />

      {/* Upsert Modal */}
      <UpsertFormModal
        open={upsertModal.open}
        onClose={handleUpsertClose}
        editingRecord={upsertModal.editingRecord}
        onSuccess={refetch}
        onToast={addToast}
      />

      {/* Bulk Import Wizard */}
      <BulkImportWizard
        open={bulkImportOpen}
        onClose={() => setBulkImportOpen(false)}
        onSuccess={refetch}
        onToast={addToast}
      />

      {/* History Panel */}
      <HistoryPanel
        open={historyModal.open}
        period={historyModal.period}
        onClose={() => setHistoryModal({ open: false, period: null })}
      />

      {/* Toast Notifications */}
      <ToastNotification toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};
