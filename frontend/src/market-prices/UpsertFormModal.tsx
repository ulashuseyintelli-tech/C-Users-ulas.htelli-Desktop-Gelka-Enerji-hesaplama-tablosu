import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useUpsertMarketPrice } from './hooks/useUpsertMarketPrice';
import { ERROR_CODE_MAP, STATUS_LABELS } from './constants';
import type {
  MarketPriceRecord,
  ToastMessage,
  UpsertFormState,
  UpsertMarketPriceRequest,
  ApiErrorResponse,
} from './types';

// =============================================================================
// Props
// =============================================================================

export interface UpsertFormModalProps {
  open: boolean;
  onClose: () => void;
  editingRecord?: MarketPriceRecord;
  onSuccess: () => void;
  onToast: (toast: ToastMessage) => void;
}

// =============================================================================
// Helpers
// =============================================================================

function getInitialFormState(record?: MarketPriceRecord): UpsertFormState {
  if (record) {
    return {
      period: record.period,
      value: String(record.ptf_tl_per_mwh),
      status: record.status,
      changeReason: '',
      sourceNote: record.source_note ?? '',
      forceUpdate: false,
    };
  }
  return {
    period: '',
    value: '',
    status: 'provisional',
    changeReason: '',
    sourceNote: '',
    forceUpdate: false,
  };
}

/** Parse user-entered value string to a number with dot decimal separator. */
function parseValueForApi(raw: string): number {
  // Replace comma with dot so Turkish-style "2508,80" becomes 2508.80
  const normalized = raw.replace(',', '.');
  return Number(normalized);
}

let toastCounter = 0;
function makeToastId(): string {
  toastCounter += 1;
  return `upsert-toast-${toastCounter}-${Date.now()}`;
}

// =============================================================================
// Component
// =============================================================================

export const UpsertFormModal: React.FC<UpsertFormModalProps> = ({
  open,
  onClose,
  editingRecord,
  onSuccess,
  onToast,
}) => {
  const { submit, loading, fieldErrors } = useUpsertMarketPrice();

  const [form, setForm] = useState<UpsertFormState>(() =>
    getInitialFormState(editingRecord),
  );
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});

  const backdropRef = useRef<HTMLDivElement>(null);

  // Reset form when modal opens or editingRecord changes
  useEffect(() => {
    if (open) {
      setForm(getInitialFormState(editingRecord));
      setShowConfirmation(false);
      setClientErrors({});
    }
  }, [open, editingRecord]);

  // Esc to close (Requirement 5.9)
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showConfirmation) {
          setShowConfirmation(false);
        } else {
          onClose();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose, showConfirmation]);

  // Merge backend field errors with client-side errors
  const allFieldErrors = { ...clientErrors, ...fieldErrors };

  // ---------------------------------------------------------------------------
  // Form field handlers
  // ---------------------------------------------------------------------------

  const updateField = useCallback(
    <K extends keyof UpsertFormState>(field: K, value: UpsertFormState[K]) => {
      setForm((prev) => ({ ...prev, [field]: value }));
      // Clear error for this field when user edits it
      setClientErrors((prev) => {
        if (prev[field]) {
          const next = { ...prev };
          delete next[field];
          return next;
        }
        return prev;
      });
    },
    [],
  );

  // ---------------------------------------------------------------------------
  // Submission logic
  // ---------------------------------------------------------------------------

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      if (e) e.preventDefault();

      // Double-submit guard (Requirement 5.5)
      if (loading) return;

      // Client-side validation: force_update requires change_reason (Requirement 5.3)
      if (form.forceUpdate && form.changeReason.trim() === '') {
        setShowConfirmation(false);
        setClientErrors({ change_reason: 'Değişiklik nedeni zorunlu' });
        return;
      }

      // If force_update is checked and confirmation not yet shown, show it
      if (form.forceUpdate && !showConfirmation) {
        setShowConfirmation(true);
        return;
      }

      // Build API request (Requirement 5.4, 5.10)
      const request: UpsertMarketPriceRequest = {
        period: form.period,
        value: parseValueForApi(form.value),
        price_type: 'PTF',
        status: form.status,
        source_note: form.sourceNote || undefined,
        change_reason: form.changeReason || undefined,
        force_update: form.forceUpdate,
      };

      try {
        const response = await submit(request);

        // Success → close + toast + refetch (Requirement 5.6)
        onClose();
        onToast({
          id: makeToastId(),
          type: 'success',
          title:
            response.action === 'created'
              ? `${response.period} dönemi oluşturuldu`
              : `${response.period} dönemi güncellendi`,
        });

        // Warnings in toast (Requirement 5.8)
        if (response.warnings && response.warnings.length > 0) {
          for (const warning of response.warnings) {
            onToast({
              id: makeToastId(),
              type: 'warning',
              title: warning,
            });
          }
        }

        onSuccess();
      } catch (err: unknown) {
        setShowConfirmation(false);

        // Extract error_code to decide field vs global toast routing
        let errorCode = '';
        if (err && typeof err === 'object' && 'response' in err) {
          const axiosErr = err as { response?: { data?: ApiErrorResponse } };
          errorCode = axiosErr.response?.data?.error_code ?? '';
        }

        // If error has no field mapping in ERROR_CODE_MAP, show global toast
        const mapping = errorCode ? ERROR_CODE_MAP[errorCode] : undefined;
        if (!mapping?.field) {
          const message = mapping?.message ?? 'Bir hata oluştu';
          onToast({
            id: makeToastId(),
            type: 'error',
            title: message,
            detail: errorCode || undefined,
          });
        }
        // Field-mapped errors are already set by the hook's fieldErrors state
      }
    },
    [form, loading, showConfirmation, submit, onClose, onToast, onSuccess],
  );

  if (!open) return null;

  const isEditing = !!editingRecord;

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === backdropRef.current) onClose();
      }}
      data-testid="upsert-modal-backdrop"
    >
      <div
        className="relative w-full max-w-lg rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={isEditing ? 'Kayıt Güncelle' : 'Yeni Kayıt'}
      >
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEditing ? 'Kayıt Güncelle' : 'Yeni Kayıt'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:text-gray-600"
            aria-label="Kapat"
          >
            ✕
          </button>
        </div>

        {/* Confirmation Dialog Overlay */}
        {showConfirmation && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-white/95">
            <div className="text-center p-6">
              <p className="mb-4 text-lg font-medium text-gray-900">
                Emin misiniz?
              </p>
              <p className="mb-6 text-sm text-gray-600">
                Bu işlem mevcut kesinleşmiş kaydı güncelleyecektir.
              </p>
              <div className="flex justify-center gap-3">
                <button
                  type="button"
                  onClick={() => setShowConfirmation(false)}
                  className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  İptal
                </button>
                <button
                  type="button"
                  onClick={() => handleSubmit()}
                  disabled={loading}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {loading ? 'Gönderiliyor...' : 'Evet, Güncelle'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit}>
          {/* Period */}
          <div className="mb-4">
            <label
              htmlFor="upsert-period"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Dönem
            </label>
            <input
              id="upsert-period"
              type="month"
              value={form.period}
              onChange={(e) => updateField('period', e.target.value)}
              disabled={isEditing}
              className={`w-full rounded-md border px-3 py-2 text-sm ${
                allFieldErrors.period
                  ? 'border-red-500'
                  : 'border-gray-300'
              } disabled:bg-gray-100`}
            />
            {allFieldErrors.period && (
              <p className="mt-1 text-xs text-red-600" data-testid="error-period">
                {allFieldErrors.period}
              </p>
            )}
          </div>

          {/* Value */}
          <div className="mb-4">
            <label
              htmlFor="upsert-value"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              PTF Değeri (TL/MWh)
            </label>
            <input
              id="upsert-value"
              type="text"
              inputMode="decimal"
              value={form.value}
              onChange={(e) => updateField('value', e.target.value)}
              placeholder="2508.80"
              className={`w-full rounded-md border px-3 py-2 text-sm ${
                allFieldErrors.value
                  ? 'border-red-500'
                  : 'border-gray-300'
              }`}
            />
            {allFieldErrors.value && (
              <p className="mt-1 text-xs text-red-600" data-testid="error-value">
                {allFieldErrors.value}
              </p>
            )}
          </div>

          {/* Status */}
          <div className="mb-4">
            <label
              htmlFor="upsert-status"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Durum
            </label>
            <select
              id="upsert-status"
              value={form.status}
              onChange={(e) =>
                updateField(
                  'status',
                  e.target.value as 'provisional' | 'final',
                )
              }
              className={`w-full rounded-md border px-3 py-2 text-sm ${
                allFieldErrors.status
                  ? 'border-red-500'
                  : 'border-gray-300'
              }`}
            >
              <option value="provisional">{STATUS_LABELS.provisional}</option>
              <option value="final">{STATUS_LABELS.final}</option>
            </select>
            {allFieldErrors.status && (
              <p className="mt-1 text-xs text-red-600" data-testid="error-status">
                {allFieldErrors.status}
              </p>
            )}
          </div>

          {/* Change Reason */}
          <div className="mb-4">
            <label
              htmlFor="upsert-change-reason"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Değişiklik Nedeni
            </label>
            <input
              id="upsert-change-reason"
              type="text"
              value={form.changeReason}
              onChange={(e) => updateField('changeReason', e.target.value)}
              className={`w-full rounded-md border px-3 py-2 text-sm ${
                allFieldErrors.change_reason
                  ? 'border-red-500'
                  : 'border-gray-300'
              }`}
            />
            {allFieldErrors.change_reason && (
              <p
                className="mt-1 text-xs text-red-600"
                data-testid="error-change_reason"
              >
                {allFieldErrors.change_reason}
              </p>
            )}
          </div>

          {/* Source Note */}
          <div className="mb-4">
            <label
              htmlFor="upsert-source-note"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Kaynak Notu
            </label>
            <input
              id="upsert-source-note"
              type="text"
              value={form.sourceNote}
              onChange={(e) => updateField('sourceNote', e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          {/* Force Update */}
          <div className="mb-6">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.forceUpdate}
                onChange={(e) => updateField('forceUpdate', e.target.checked)}
                className="rounded border-gray-300"
              />
              Zorla Güncelle (force_update)
            </label>
            {allFieldErrors.force_update && (
              <p
                className="mt-1 text-xs text-red-600"
                data-testid="error-force_update"
              >
                {allFieldErrors.force_update}
              </p>
            )}
          </div>

          {/* Submit */}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              İptal
            </button>
            <button
              type="submit"
              disabled={loading}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? 'Gönderiliyor...' : isEditing ? 'Güncelle' : 'Kaydet'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
