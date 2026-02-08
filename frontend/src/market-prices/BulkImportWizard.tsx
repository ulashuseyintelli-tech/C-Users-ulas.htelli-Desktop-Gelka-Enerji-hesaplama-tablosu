import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useBulkImportPreview } from './hooks/useBulkImportPreview';
import { useBulkImportApply } from './hooks/useBulkImportApply';
import { exportFailedRowsCsv, exportFailedRowsJson } from './utils';
import type {
  ToastMessage,
  BulkImportStep,
  BulkImportPreviewResponse,
  BulkImportApplyResponse,
  BulkImportError,
} from './types';

// =============================================================================
// Props
// =============================================================================

export interface BulkImportWizardProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  onToast: (toast: ToastMessage) => void;
}

// =============================================================================
// Helpers
// =============================================================================

let toastCounter = 0;
function makeToastId(): string {
  toastCounter += 1;
  return `bulk-toast-${toastCounter}-${Date.now()}`;
}

/** Trigger a browser file download from a string blob. */
function downloadBlob(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// =============================================================================
// Step Indicator
// =============================================================================

const STEP_LABELS: Record<BulkImportStep, string> = {
  upload: '1. Dosya Yükle',
  preview: '2. Önizleme',
  result: '3. Sonuç',
};

const STEP_ORDER: BulkImportStep[] = ['upload', 'preview', 'result'];

function StepIndicator({ current }: { current: BulkImportStep }) {
  const currentIdx = STEP_ORDER.indexOf(current);
  return (
    <div className="mb-6 flex items-center gap-2" data-testid="step-indicator">
      {STEP_ORDER.map((step, idx) => {
        const isActive = idx === currentIdx;
        const isCompleted = idx < currentIdx;
        return (
          <React.Fragment key={step}>
            {idx > 0 && (
              <div
                className={`h-0.5 flex-1 ${
                  isCompleted ? 'bg-blue-500' : 'bg-gray-200'
                }`}
              />
            )}
            <span
              className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${
                isActive
                  ? 'bg-blue-100 text-blue-700'
                  : isCompleted
                    ? 'bg-green-100 text-green-700'
                    : 'bg-gray-100 text-gray-500'
              }`}
            >
              {STEP_LABELS[step]}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

// =============================================================================
// Component
// =============================================================================

export const BulkImportWizard: React.FC<BulkImportWizardProps> = ({
  open,
  onClose,
  onSuccess,
  onToast,
}) => {
  // ---- Hooks ----
  const {
    preview: previewFn,
    loading: previewLoading,
    error: previewError,
  } = useBulkImportPreview();
  const {
    apply: applyFn,
    loading: applyLoading,
    error: applyError,
  } = useBulkImportApply();

  // ---- State ----
  const [step, setStep] = useState<BulkImportStep>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [forceUpdate, setForceUpdate] = useState(false);
  const [previewData, setPreviewData] = useState<BulkImportPreviewResponse | null>(null);
  const [applyData, setApplyData] = useState<BulkImportApplyResponse | null>(null);

  const backdropRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ---- Reset on open ----
  useEffect(() => {
    if (open) {
      setStep('upload');
      setFile(null);
      setForceUpdate(false);
      setPreviewData(null);
      setApplyData(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, [open]);

  // ---- Esc to close ----
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // ---- File selection handler ----
  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = e.target.files?.[0] ?? null;
      setFile(selected);
    },
    [],
  );

  // ---- Upload → Preview (Requirement 6.1) ----
  const handlePreview = useCallback(async () => {
    if (!file) return;
    try {
      const response = await previewFn(file, 'PTF', forceUpdate);
      setPreviewData(response);
      setStep('preview');
    } catch {
      onToast({
        id: makeToastId(),
        type: 'error',
        title: 'Önizleme başarısız',
        detail: previewError?.error_code,
      });
    }
  }, [file, forceUpdate, previewFn, onToast, previewError]);

  // ---- Apply (Requirement 7.1) ----
  const handleApply = useCallback(async () => {
    if (!file || applyLoading) return; // Double-submit guard (Requirement 7.2)
    try {
      const response = await applyFn(file, 'PTF', forceUpdate, true);
      setApplyData(response);
      setStep('result');

      // Success → refetch price list (Requirement 7.5)
      if (response.result.success || response.result.imported_count > 0) {
        onSuccess();
      }

      onToast({
        id: makeToastId(),
        type: response.result.error_count > 0 ? 'warning' : 'success',
        title: `Import tamamlandı: ${response.result.imported_count} başarılı, ${response.result.skipped_count} atlandı, ${response.result.error_count} hata`,
      });
    } catch {
      onToast({
        id: makeToastId(),
        type: 'error',
        title: 'Import uygulama başarısız',
        detail: applyError?.error_code,
      });
    }
  }, [file, forceUpdate, applyFn, applyLoading, onSuccess, onToast, applyError]);

  // ---- Download failed rows (Requirement 7.4) ----
  const handleDownloadCsv = useCallback(
    (errors: BulkImportError[]) => {
      const csv = exportFailedRowsCsv(errors);
      downloadBlob(csv, 'failed_rows.csv', 'text/csv;charset=utf-8');
    },
    [],
  );

  const handleDownloadJson = useCallback(
    (errors: BulkImportError[]) => {
      const json = exportFailedRowsJson(errors);
      downloadBlob(json, 'failed_rows.json', 'application/json;charset=utf-8');
    },
    [],
  );

  if (!open) return null;

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === backdropRef.current) onClose();
      }}
      data-testid="bulk-import-backdrop"
    >
      <div
        className="relative w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label="Toplu Import"
      >
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Toplu Import</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:text-gray-600"
            aria-label="Kapat"
          >
            ✕
          </button>
        </div>

        {/* Step Indicator */}
        <StepIndicator current={step} />

        {/* Step Content */}
        {step === 'upload' && (
          <UploadStep
            file={file}
            forceUpdate={forceUpdate}
            loading={previewLoading}
            fileInputRef={fileInputRef}
            onFileChange={handleFileChange}
            onForceUpdateChange={setForceUpdate}
            onPreview={handlePreview}
            onClose={onClose}
          />
        )}

        {step === 'preview' && previewData && (
          <PreviewStep
            preview={previewData}
            loading={applyLoading}
            onApply={handleApply}
            onBack={() => setStep('upload')}
          />
        )}

        {step === 'result' && applyData && (
          <ResultStep
            result={applyData}
            onDownloadCsv={handleDownloadCsv}
            onDownloadJson={handleDownloadJson}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
};

// =============================================================================
// Step 1: Upload
// =============================================================================

interface UploadStepProps {
  file: File | null;
  forceUpdate: boolean;
  loading: boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onForceUpdateChange: (checked: boolean) => void;
  onPreview: () => void;
  onClose: () => void;
}

function UploadStep({
  file,
  forceUpdate,
  loading,
  fileInputRef,
  onFileChange,
  onForceUpdateChange,
  onPreview,
  onClose,
}: UploadStepProps) {
  return (
    <div data-testid="upload-step">
      {/* File Input */}
      <div className="mb-4">
        <label
          htmlFor="bulk-file"
          className="mb-1 block text-sm font-medium text-gray-700"
        >
          Dosya Seçin (CSV veya JSON)
        </label>
        <input
          id="bulk-file"
          ref={fileInputRef}
          type="file"
          accept=".csv,.json"
          onChange={onFileChange}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-blue-50 file:px-3 file:py-1 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100"
          data-testid="file-input"
        />
        {file && (
          <p className="mt-1 text-xs text-gray-500">
            Seçilen dosya: {file.name} ({(file.size / 1024).toFixed(1)} KB)
          </p>
        )}
      </div>

      {/* Price Type (fixed) */}
      <div className="mb-4">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Fiyat Tipi
        </label>
        <input
          type="text"
          value="PTF"
          disabled
          className="w-full rounded-md border border-gray-300 bg-gray-100 px-3 py-2 text-sm"
        />
      </div>

      {/* Force Update Checkbox */}
      <div className="mb-6">
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={forceUpdate}
            onChange={(e) => onForceUpdateChange(e.target.checked)}
            className="rounded border-gray-300"
            data-testid="force-update-checkbox"
          />
          Zorla Güncelle (force_update)
        </label>
        <p className="mt-1 text-xs text-gray-500">
          İşaretlenirse kesinleşmiş kayıtlar da güncellenebilir.
        </p>
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          İptal
        </button>
        <button
          type="button"
          onClick={onPreview}
          disabled={!file || loading}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="preview-button"
        >
          {loading ? 'Yükleniyor...' : 'Önizleme'}
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Step 2: Preview
// =============================================================================

interface PreviewStepProps {
  preview: BulkImportPreviewResponse;
  loading: boolean;
  onApply: () => void;
  onBack: () => void;
}

function PreviewStep({ preview, loading, onApply, onBack }: PreviewStepProps) {
  const p = preview.preview;

  return (
    <div data-testid="preview-step">
      {/* Summary Counts (Requirement 6.2) */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryCard label="Toplam Satır" value={p.total_rows} />
        <SummaryCard label="Geçerli" value={p.valid_rows} color="green" />
        <SummaryCard label="Geçersiz" value={p.invalid_rows} color="red" />
        <SummaryCard label="Yeni Kayıt" value={p.new_records} color="blue" />
        <SummaryCard label="Güncelleme" value={p.updates} color="blue" />
        <SummaryCard label="Değişiklik Yok" value={p.unchanged} />
        <SummaryCard
          label="Final Çakışma"
          value={p.final_conflicts}
          color={p.final_conflicts > 0 ? 'amber' : undefined}
        />
      </div>

      {/* Final Conflicts Warning (Requirement 6.4) */}
      {p.final_conflicts > 0 && (
        <div
          className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700"
          data-testid="final-conflicts-warning"
        >
          ⚠️ {p.final_conflicts} adet kesinleşmiş kayıt korunuyor.
          Bu kayıtları güncellemek için &quot;Zorla Güncelle&quot; seçeneğini etkinleştirin.
        </div>
      )}

      {/* Per-Row Error List (Requirement 6.3) */}
      {p.errors.length > 0 && (
        <div className="mb-4" data-testid="error-list">
          <h3 className="mb-2 text-sm font-medium text-gray-900">
            Hatalar ({p.errors.length})
          </h3>
          <div className="max-h-48 overflow-y-auto rounded-md border border-red-200">
            <table className="w-full text-sm">
              <thead className="bg-red-50 text-left text-xs text-red-700">
                <tr>
                  <th className="px-3 py-2">Satır</th>
                  <th className="px-3 py-2">Alan</th>
                  <th className="px-3 py-2">Hata</th>
                </tr>
              </thead>
              <tbody>
                {p.errors.map((err, idx) => (
                  <tr
                    key={`${err.row}-${err.field}-${idx}`}
                    className="border-t border-red-100"
                  >
                    <td className="px-3 py-1.5 text-gray-700">{err.row}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-gray-600">
                      {err.field}
                    </td>
                    <td className="px-3 py-1.5 text-red-600">{err.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Preview Disclaimer (Design Decision 5) */}
      <p className="mb-4 text-xs text-gray-500 italic">
        Önizleme tahminidir. Uygulama sonucu farklılık gösterebilir.
      </p>

      {/* Actions */}
      <div className="flex justify-end gap-3">
        <button
          type="button"
          onClick={onBack}
          disabled={loading}
          className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          Geri
        </button>
        <button
          type="button"
          onClick={onApply}
          disabled={loading}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="apply-button"
        >
          {loading ? 'Uygulanıyor...' : 'Uygula'}
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Step 3: Result
// =============================================================================

interface ResultStepProps {
  result: BulkImportApplyResponse;
  onDownloadCsv: (errors: BulkImportError[]) => void;
  onDownloadJson: (errors: BulkImportError[]) => void;
  onClose: () => void;
}

function ResultStep({ result, onDownloadCsv, onDownloadJson, onClose }: ResultStepProps) {
  const r = result.result;
  const hasFailedRows = r.details && r.details.length > 0;

  return (
    <div data-testid="result-step">
      {/* Result Summary (Requirement 7.3) */}
      <div className="mb-4 grid grid-cols-3 gap-3">
        <SummaryCard label="Başarılı" value={r.imported_count} color="green" />
        <SummaryCard label="Atlanan" value={r.skipped_count} color="amber" />
        <SummaryCard label="Hata" value={r.error_count} color="red" />
      </div>

      {/* Overall Status */}
      {r.error_count === 0 && r.imported_count > 0 && (
        <div className="mb-4 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">
          ✅ Import başarıyla tamamlandı.
        </div>
      )}

      {r.error_count > 0 && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          ⚠️ {r.error_count} satırda hata oluştu.
        </div>
      )}

      {/* Failed Rows Download (Requirement 7.4) */}
      {hasFailedRows && (
        <div className="mb-4" data-testid="failed-rows-download">
          <h3 className="mb-2 text-sm font-medium text-gray-900">
            Başarısız Satırları İndir
          </h3>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => onDownloadCsv(r.details)}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              data-testid="download-csv-button"
            >
              CSV İndir
            </button>
            <button
              type="button"
              onClick={() => onDownloadJson(r.details)}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              data-testid="download-json-button"
            >
              JSON İndir
            </button>
          </div>
        </div>
      )}

      {/* Failed Rows Detail Table */}
      {hasFailedRows && (
        <div className="mb-4" data-testid="failed-rows-table">
          <h3 className="mb-2 text-sm font-medium text-gray-900">
            Hata Detayları ({r.details.length})
          </h3>
          <div className="max-h-48 overflow-y-auto rounded-md border border-red-200">
            <table className="w-full text-sm">
              <thead className="bg-red-50 text-left text-xs text-red-700">
                <tr>
                  <th className="px-3 py-2">Satır</th>
                  <th className="px-3 py-2">Alan</th>
                  <th className="px-3 py-2">Hata</th>
                </tr>
              </thead>
              <tbody>
                {r.details.map((err, idx) => (
                  <tr
                    key={`${err.row}-${err.field}-${idx}`}
                    className="border-t border-red-100"
                  >
                    <td className="px-3 py-1.5 text-gray-700">{err.row}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-gray-600">
                      {err.field}
                    </td>
                    <td className="px-3 py-1.5 text-red-600">{err.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Close */}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          data-testid="close-button"
        >
          Kapat
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Summary Card (reusable)
// =============================================================================

interface SummaryCardProps {
  label: string;
  value: number;
  color?: 'green' | 'red' | 'blue' | 'amber';
}

function SummaryCard({ label, value, color }: SummaryCardProps) {
  const colorClasses: Record<string, string> = {
    green: 'bg-green-50 border-green-200 text-green-700',
    red: 'bg-red-50 border-red-200 text-red-700',
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    amber: 'bg-amber-50 border-amber-200 text-amber-700',
  };

  const classes = color
    ? colorClasses[color]
    : 'bg-gray-50 border-gray-200 text-gray-700';

  return (
    <div className={`rounded-md border p-3 text-center ${classes}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
