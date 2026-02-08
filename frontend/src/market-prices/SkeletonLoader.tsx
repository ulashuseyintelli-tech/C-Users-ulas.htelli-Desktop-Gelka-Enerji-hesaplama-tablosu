import React from 'react';

export interface SkeletonLoaderProps {
  /** Number of skeleton rows to display. Defaults to 5. */
  rows?: number;
}

/**
 * Table skeleton with shimmer animation matching PriceListTable column layout.
 * Uses Tailwind animate-pulse for the shimmer effect.
 *
 * Columns (8): Dönem, PTF (TL/MWh), Durum, Güncelleme, Kaynak, Güncelleyen, Değişiklik Nedeni, İşlem
 */
export const SkeletonLoader: React.FC<SkeletonLoaderProps> = ({ rows = 5 }) => {
  // Column widths approximate the real table layout
  const columns = [
    'w-20',  // Dönem
    'w-24',  // PTF (TL/MWh)
    'w-20',  // Durum
    'w-32',  // Güncelleme
    'w-20',  // Kaynak
    'w-24',  // Güncelleyen
    'w-32',  // Değişiklik Nedeni
    'w-16',  // İşlem
  ];

  return (
    <div className="overflow-x-auto" role="status" aria-label="Yükleniyor">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((_, idx) => (
              <th key={idx} className="px-4 py-3">
                <div className={`h-4 bg-gray-200 rounded animate-pulse ${columns[idx]}`} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 bg-white">
          {Array.from({ length: rows }, (_, rowIdx) => (
            <tr key={rowIdx}>
              {columns.map((colWidth, colIdx) => (
                <td key={colIdx} className="px-4 py-3">
                  <div className={`h-4 bg-gray-100 rounded animate-pulse ${colWidth}`} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
