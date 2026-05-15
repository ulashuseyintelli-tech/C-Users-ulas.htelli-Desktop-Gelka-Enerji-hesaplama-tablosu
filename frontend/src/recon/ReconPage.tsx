import { useState, useRef, useCallback } from 'react';
import { ArrowLeft, Upload, Loader2, ChevronDown, ChevronUp, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';
import { analyzeRecon, ReconApiError } from './reconApi';
import { ReconReport, ReconRequest, InvoiceInput, PeriodResult, ReconciliationItem } from './types';

// ═══════════════════════════════════════════════════════════════════════════════
// Fatura Mutabakat Analizi — Upload & Results Page
// ═══════════════════════════════════════════════════════════════════════════════

interface ReconPageProps {
  onBack: () => void;
}

export default function ReconPage({ onBack }: ReconPageProps) {
  // File state
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Invoice input (optional, collapsible)
  const [showInvoiceInput, setShowInvoiceInput] = useState(false);
  const [invoiceInput, setInvoiceInput] = useState<InvoiceInput>({
    period: '',
    unit_price: undefined,
    discount_pct: undefined,
    distribution_unit_price: undefined,
    declared_t1_kwh: undefined,
    declared_t2_kwh: undefined,
    declared_t3_kwh: undefined,
    declared_total_kwh: undefined,
  });

  // Request state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReconReport | null>(null);

  // ── File Handling ──

  const handleFileSelect = useCallback((selectedFile: File) => {
    setFile(selectedFile);
    setError(null);
    setReport(null);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile) handleFileSelect(droppedFile);
  }, [handleFileSelect]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragActive(false);
  }, []);

  // ── Submit ──

  const handleSubmit = useCallback(async () => {
    if (!file) return;

    setLoading(true);
    setError(null);
    setReport(null);

    try {
      // Build request body if invoice input is provided
      let requestBody: ReconRequest | undefined;
      if (showInvoiceInput && invoiceInput.period) {
        requestBody = { invoice_input: invoiceInput };
      }

      const result = await analyzeRecon(file, requestBody);
      setReport(result);
    } catch (err) {
      if (err instanceof ReconApiError) {
        setError(err.message);
      } else {
        setError('Beklenmeyen bir hata oluştu.');
      }
    } finally {
      setLoading(false);
    }
  }, [file, showInvoiceInput, invoiceInput]);

  // ── Invoice Input Helpers ──

  const updateInvoiceField = (field: keyof InvoiceInput, value: string) => {
    setInvoiceInput(prev => ({
      ...prev,
      [field]: value === '' ? undefined : (field === 'period' ? value : parseFloat(value)),
    }));
  };

  // ── Render ──

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 flex-shrink-0">
        <div className="max-w-5xl mx-auto px-4 py-3">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              title="Geri"
            >
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <h1 className="text-lg font-bold text-gray-900">Fatura Mutabakat Analizi</h1>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-5xl mx-auto px-4 py-6 w-full space-y-6">
        {/* File Upload Section */}
        <section className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Excel Dosyası Yükle</h2>
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              dragActive
                ? 'border-blue-400 bg-blue-50'
                : file
                ? 'border-green-300 bg-green-50'
                : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50'
            }`}
          >
            <Upload className={`w-8 h-8 mx-auto mb-2 ${file ? 'text-green-500' : 'text-gray-400'}`} />
            {file ? (
              <p className="text-sm text-green-700 font-medium">{file.name}</p>
            ) : (
              <>
                <p className="text-sm text-gray-600">Dosyayı sürükleyin veya tıklayın</p>
                <p className="text-xs text-gray-400 mt-1">.xlsx veya .xls (maks. 50 MB)</p>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFileSelect(f);
            }}
          />
        </section>

        {/* Optional Invoice Input (Collapsible) */}
        <section className="bg-white rounded-xl border border-gray-200">
          <button
            onClick={() => setShowInvoiceInput(!showInvoiceInput)}
            className="w-full px-6 py-3 flex items-center justify-between text-left"
          >
            <span className="text-sm font-semibold text-gray-700">Fatura Bilgileri (Opsiyonel)</span>
            {showInvoiceInput ? (
              <ChevronUp className="w-4 h-4 text-gray-400" />
            ) : (
              <ChevronDown className="w-4 h-4 text-gray-400" />
            )}
          </button>
          {showInvoiceInput && (
            <div className="px-6 pb-5 grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Dönem (YYYY-MM)</label>
                <input
                  type="text"
                  placeholder="2026-04"
                  value={invoiceInput.period || ''}
                  onChange={(e) => updateInvoiceField('period', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Birim Fiyat (TL/kWh)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.unit_price ?? ''}
                  onChange={(e) => updateInvoiceField('unit_price', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">İndirim (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={invoiceInput.discount_pct ?? ''}
                  onChange={(e) => updateInvoiceField('discount_pct', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Dağıtım B.F. (TL/kWh)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.distribution_unit_price ?? ''}
                  onChange={(e) => updateInvoiceField('distribution_unit_price', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T1 kWh (Beyan)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.declared_t1_kwh ?? ''}
                  onChange={(e) => updateInvoiceField('declared_t1_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T2 kWh (Beyan)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.declared_t2_kwh ?? ''}
                  onChange={(e) => updateInvoiceField('declared_t2_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T3 kWh (Beyan)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.declared_t3_kwh ?? ''}
                  onChange={(e) => updateInvoiceField('declared_t3_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Toplam kWh (Beyan)</label>
                <input
                  type="number"
                  step="0.01"
                  value={invoiceInput.declared_total_kwh ?? ''}
                  onChange={(e) => updateInvoiceField('declared_total_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            </div>
          )}
        </section>

        {/* Submit Button */}
        <button
          onClick={handleSubmit}
          disabled={!file || loading}
          className="w-full py-3 bg-blue-600 text-white font-semibold rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />
              Analiz ediliyor...
            </span>
          ) : (
            'Analiz Et'
          )}
        </button>

        {/* Error State (Red) */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
            <XCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {/* Results */}
        {report && <ReconResults report={report} />}
      </main>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Results Component
// ═══════════════════════════════════════════════════════════════════════════════

function ReconResults({ report }: { report: ReconReport }) {
  const isPartial = report.status === 'partial';
  const hasBlockedPeriods = report.periods.some(p => p.quote_blocked);

  return (
    <div className="space-y-4">
      {/* Status Banner */}
      {isPartial && hasBlockedPeriods ? (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-amber-700">Kısmi Sonuç</p>
            <p className="text-xs text-amber-600 mt-1">
              Bazı dönemler için teklif oluşturulamıyor. Detaylar aşağıda.
            </p>
          </div>
        </div>
      ) : (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-start gap-3">
          <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-green-700">Analiz Tamamlandı</p>
            <p className="text-xs text-green-600 mt-1">
              Tüm dönemler başarıyla analiz edildi.
            </p>
          </div>
        </div>
      )}

      {/* Parse Stats */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Dosya Bilgileri</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Format:</span>
            <span className="ml-2 font-medium text-gray-900">{report.format_detected}</span>
          </div>
          <div>
            <span className="text-gray-500">Toplam Satır:</span>
            <span className="ml-2 font-medium text-gray-900">{report.parse_stats.total_rows}</span>
          </div>
          <div>
            <span className="text-gray-500">Başarılı:</span>
            <span className="ml-2 font-medium text-green-700">{report.parse_stats.parsed_rows}</span>
          </div>
          <div>
            <span className="text-gray-500">Hatalı:</span>
            <span className={`ml-2 font-medium ${report.parse_stats.error_rows > 0 ? 'text-red-700' : 'text-gray-900'}`}>
              {report.parse_stats.error_rows}
            </span>
          </div>
        </div>
        {report.parse_stats.errors.length > 0 && (
          <div className="mt-3 text-xs text-red-600 space-y-1">
            {report.parse_stats.errors.slice(0, 5).map((err, i) => (
              <p key={i}>• {err}</p>
            ))}
            {report.parse_stats.errors.length > 5 && (
              <p className="text-gray-500">...ve {report.parse_stats.errors.length - 5} hata daha</p>
            )}
          </div>
        )}
      </div>

      {/* Period Results */}
      {report.periods.map((period, idx) => (
        <PeriodCard key={idx} period={period} isPartial={isPartial} />
      ))}

      {/* Warnings */}
      {report.warnings.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-amber-700 mb-2">Uyarılar</h3>
          <ul className="text-xs text-amber-600 space-y-1">
            {report.warnings.map((w, i) => (
              <li key={i}>• {w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Period Card Component
// ═══════════════════════════════════════════════════════════════════════════════

function PeriodCard({ period, isPartial }: { period: PeriodResult; isPartial: boolean }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
      {/* Period Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Dönem: {period.period}</h3>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
          period.overall_severity === 'CRITICAL'
            ? 'bg-red-100 text-red-700'
            : period.overall_severity === 'WARNING'
            ? 'bg-amber-100 text-amber-700'
            : 'bg-green-100 text-green-700'
        }`}>
          {period.overall_status}
        </span>
      </div>

      {/* kWh Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
        <div>
          <span className="text-gray-500 text-xs">Toplam</span>
          <p className="font-medium text-gray-900">{period.total_kwh.toLocaleString('tr-TR')} kWh</p>
        </div>
        <div>
          <span className="text-gray-500 text-xs">T1 (Gündüz)</span>
          <p className="font-medium text-gray-900">{period.t1_kwh.toLocaleString('tr-TR')} kWh</p>
          <p className="text-xs text-gray-400">%{period.t1_pct.toFixed(1)}</p>
        </div>
        <div>
          <span className="text-gray-500 text-xs">T2 (Puant)</span>
          <p className="font-medium text-gray-900">{period.t2_kwh.toLocaleString('tr-TR')} kWh</p>
          <p className="text-xs text-gray-400">%{period.t2_pct.toFixed(1)}</p>
        </div>
        <div>
          <span className="text-gray-500 text-xs">T3 (Gece)</span>
          <p className="font-medium text-gray-900">{period.t3_kwh.toLocaleString('tr-TR')} kWh</p>
          <p className="text-xs text-gray-400">%{period.t3_pct.toFixed(1)}</p>
        </div>
        <div>
          <span className="text-gray-500 text-xs">Eksik Saat</span>
          <p className={`font-medium ${period.missing_hours > 0 ? 'text-amber-700' : 'text-gray-900'}`}>
            {period.missing_hours}
          </p>
        </div>
      </div>

      {/* Reconciliation Items */}
      {period.reconciliation.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-gray-600 mb-2">Mutabakat Detayı</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left py-1.5 text-gray-500 font-medium">Alan</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Excel (kWh)</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Fatura (kWh)</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Fark</th>
                  <th className="text-right py-1.5 text-gray-500 font-medium">Fark %</th>
                  <th className="text-center py-1.5 text-gray-500 font-medium">Durum</th>
                </tr>
              </thead>
              <tbody>
                {period.reconciliation.map((item, i) => (
                  <ReconciliationRow key={i} item={item} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Cost Comparison (only when status="ok" and cost_comparison exists) */}
      {!isPartial && period.cost_comparison && (
        <div className="bg-gray-50 rounded-lg p-4">
          <h4 className="text-xs font-semibold text-gray-600 mb-2">Maliyet Karşılaştırması</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div>
              <span className="text-gray-500 text-xs">Fatura Toplam</span>
              <p className="font-medium text-gray-900">
                {period.cost_comparison.invoice_total_tl.toLocaleString('tr-TR', { minimumFractionDigits: 2 })} ₺
              </p>
            </div>
            <div>
              <span className="text-gray-500 text-xs">Gelka Toplam</span>
              <p className="font-medium text-gray-900">
                {period.cost_comparison.gelka_total_tl.toLocaleString('tr-TR', { minimumFractionDigits: 2 })} ₺
              </p>
            </div>
            <div>
              <span className="text-gray-500 text-xs">Fark</span>
              <p className={`font-medium ${period.cost_comparison.difference_tl >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                {period.cost_comparison.difference_tl.toLocaleString('tr-TR', { minimumFractionDigits: 2 })} ₺
              </p>
            </div>
            <div>
              <span className="text-gray-500 text-xs">Fark %</span>
              <p className={`font-medium ${period.cost_comparison.difference_pct >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                %{period.cost_comparison.difference_pct.toFixed(2)}
              </p>
            </div>
          </div>
          {period.cost_comparison.message && (
            <p className="text-xs text-gray-600 mt-2">{period.cost_comparison.message}</p>
          )}
        </div>
      )}

      {/* Quote Blocked Banner (amber) */}
      {period.quote_blocked && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-xs font-medium text-amber-700">Teklif Oluşturulamıyor</p>
            {period.quote_block_reason && (
              <p className="text-xs text-amber-600 mt-0.5">{period.quote_block_reason}</p>
            )}
          </div>
        </div>
      )}

      {/* Period Warnings */}
      {period.warnings.length > 0 && (
        <div className="text-xs text-amber-600 space-y-0.5">
          {period.warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Reconciliation Row
// ═══════════════════════════════════════════════════════════════════════════════

function ReconciliationRow({ item }: { item: ReconciliationItem }) {
  const severityBadge = () => {
    if (!item.severity) return null;
    const colors = {
      LOW: 'bg-green-100 text-green-700',
      WARNING: 'bg-amber-100 text-amber-700',
      CRITICAL: 'bg-red-100 text-red-700',
    };
    return (
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[item.severity]}`}>
        {item.severity}
      </span>
    );
  };

  const statusColor = {
    UYUMLU: 'text-green-700',
    UYUMSUZ: 'text-red-700',
    KONTROL_EDILMEDI: 'text-gray-500',
  };

  return (
    <tr className="border-b border-gray-50">
      <td className="py-1.5 text-gray-900">{item.field}</td>
      <td className="py-1.5 text-right text-gray-700">{item.excel_total_kwh.toLocaleString('tr-TR')}</td>
      <td className="py-1.5 text-right text-gray-700">{item.invoice_total_kwh.toLocaleString('tr-TR')}</td>
      <td className="py-1.5 text-right text-gray-700">{item.delta_kwh.toLocaleString('tr-TR')}</td>
      <td className="py-1.5 text-right text-gray-700">%{item.delta_pct.toFixed(2)}</td>
      <td className="py-1.5 text-center">
        <div className="flex items-center justify-center gap-1">
          <span className={`text-[10px] font-medium ${statusColor[item.status]}`}>{item.status}</span>
          {severityBadge()}
        </div>
      </td>
    </tr>
  );
}
