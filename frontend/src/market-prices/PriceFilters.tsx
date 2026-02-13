import React, { useRef, useCallback, useEffect } from 'react';
import type { FilterState } from './types';
import { STATUS_LABELS } from './constants';
import { trackEvent } from './telemetry';

export interface PriceFiltersProps {
  filters: FilterState;
  onFilterChange: (filters: Partial<FilterState>) => void;
}

const DEBOUNCE_MS = 300;

/**
 * Filter controls for the market prices list.
 *
 * - Status dropdown: "Tümü" (all), "Ön Değer" (provisional), "Kesinleşmiş" (final)
 * - from_period and to_period YYYY-MM inputs (type="month")
 * - 300ms debounce on change before calling onFilterChange
 * - Parent handles page reset to 1 via onFilterChange callback
 */
export const PriceFilters: React.FC<PriceFiltersProps> = ({
  filters,
  onFilterChange,
}) => {
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const debouncedChange = useCallback(
    (update: Partial<FilterState>) => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
      debounceRef.current = setTimeout(() => {
        // Telemetry: filter change — Requirement 5.8
        trackEvent('ptf_admin.filter_change', { ...filters, ...update });
        onFilterChange(update);
      }, DEBOUNCE_MS);
    },
    [onFilterChange, filters],
  );

  const handleStatusChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const value = e.target.value as FilterState['status'];
    debouncedChange({ status: value });
  };

  const handleFromPeriodChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    debouncedChange({ fromPeriod: e.target.value });
  };

  const handleToPeriodChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    debouncedChange({ toPeriod: e.target.value });
  };

  return (
    <div className="flex flex-wrap items-end gap-4">
      {/* Status dropdown */}
      <div className="flex flex-col gap-1">
        <label htmlFor="filter-status" className="text-sm font-medium text-gray-700">
          Durum
        </label>
        <select
          id="filter-status"
          value={filters.status}
          onChange={handleStatusChange}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="all">Tümü</option>
          <option value="provisional">{STATUS_LABELS.provisional}</option>
          <option value="final">{STATUS_LABELS.final}</option>
        </select>
      </div>

      {/* From period */}
      <div className="flex flex-col gap-1">
        <label htmlFor="filter-from-period" className="text-sm font-medium text-gray-700">
          Başlangıç Dönemi
        </label>
        <input
          id="filter-from-period"
          type="month"
          value={filters.fromPeriod}
          onChange={handleFromPeriodChange}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {/* To period */}
      <div className="flex flex-col gap-1">
        <label htmlFor="filter-to-period" className="text-sm font-medium text-gray-700">
          Bitiş Dönemi
        </label>
        <input
          id="filter-to-period"
          type="month"
          value={filters.toPeriod}
          onChange={handleToPeriodChange}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>
    </div>
  );
};
