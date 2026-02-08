import React from 'react';
import { STATUS_LABELS } from './constants';

export interface StatusBadgeProps {
  status: 'provisional' | 'final';
}

const STATUS_STYLES: Record<'provisional' | 'final', string> = {
  provisional: 'bg-amber-100 text-amber-700',
  final: 'bg-green-100 text-green-700',
};

/**
 * Displays a colored badge for market price record status.
 * - provisional → "Ön Değer" (amber)
 * - final → "Kesinleşmiş" (green)
 */
export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
};
