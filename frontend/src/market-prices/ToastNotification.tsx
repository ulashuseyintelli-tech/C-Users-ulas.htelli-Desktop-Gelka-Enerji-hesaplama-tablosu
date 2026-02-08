import React, { useEffect, useCallback } from 'react';
import type { ToastMessage } from './types';

export interface ToastNotificationProps {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}

const TOAST_STYLES: Record<ToastMessage['type'], string> = {
  success: 'bg-green-50 border-green-200 text-green-700',
  info: 'bg-blue-50 border-blue-200 text-blue-700',
  warning: 'bg-amber-50 border-amber-200 text-amber-700',
  error: 'bg-red-50 border-red-200 text-red-700',
};

const DEFAULT_AUTO_CLOSE = 5000;

/**
 * Single toast item with auto-close and dismiss button.
 */
const ToastItem: React.FC<{
  toast: ToastMessage;
  onDismiss: (id: string) => void;
}> = ({ toast, onDismiss }) => {
  const handleDismiss = useCallback(() => {
    onDismiss(toast.id);
  }, [onDismiss, toast.id]);

  useEffect(() => {
    const duration = toast.autoClose ?? DEFAULT_AUTO_CLOSE;
    if (duration <= 0) return;

    const timer = setTimeout(handleDismiss, duration);
    return () => clearTimeout(timer);
  }, [toast.autoClose, handleDismiss]);

  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-lg border p-4 shadow-sm ${TOAST_STYLES[toast.type]}`}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.detail && (
          <p className="mt-1 text-xs font-mono opacity-75">{toast.detail}</p>
        )}
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        className="flex-shrink-0 inline-flex rounded-md p-1 hover:opacity-70 focus:outline-none focus:ring-2 focus:ring-offset-2"
        aria-label="Kapat"
      >
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  );
};

/**
 * Toast notification container. Renders a stack of toast messages
 * with success/info/warning/error variants.
 *
 * - Auto-close after 5s by default (configurable via autoClose prop on ToastMessage)
 * - error_code (detail) displayed in monospace font for debugging
 * - Dismiss button (X) on each toast
 */
export const ToastNotification: React.FC<ToastNotificationProps> = ({
  toasts,
  onDismiss,
}) => {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-96 max-w-full">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
};
