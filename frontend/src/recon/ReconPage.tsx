import { useState, useRef, useCallback } from 'react';
import { ArrowLeft, Upload, Loader2, ChevronDown, ChevronUp, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';
import { analyzeRecon, ReconApiError } from './reconApi';
import { ReconReport, ReconRequest, InvoiceInput, PeriodResult } from './types';
import { parseTurkishNumber } from './numberFormat';
import { PeriodCardV2 } from './PeriodCardV2';
import { MultiPeriodSummaryV2 } from './MultiPeriodSummaryV2';

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
  // NOTE: All numeric fields stored as raw strings so users can type Turkish format
  // ("32.257,08", "3,08", "1.21167" etc.). Parsed via parseTurkishNumber on submit.
  const [showInvoiceInput, setShowInvoiceInput] = useState(false);
  const [invoiceInput, setInvoiceInput] = useState({
    period: '',
    unit_price: '',
    discount_pct: '',
    distribution_unit_price: '',
    declared_t1_kwh: '',
    declared_t2_kwh: '',
    declared_t3_kwh: '',
    declared_total_kwh: '',
    declared_total_tl: '',  // v2 — energy bill total in TL (drives cost headline)
  });

  // Request state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReconReport | null>(null);
  // Snapshot of invoices submitted with the latest analyze call. Used by the
  // results renderer to surface declared_total_tl in the cost headline cell
  // (backend echoes it indirectly via supplier_markup_tl, but the headline
  // needs the raw figure even when ref cost is null — REQ-6.1).
  const [submittedInvoices, setSubmittedInvoices] = useState<InvoiceInput[]>([]);

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
      let invoicesForRender: InvoiceInput[] = [];
      if (showInvoiceInput && invoiceInput.period) {
        const invoice: InvoiceInput = {
          period: invoiceInput.period,
          unit_price_tl_per_kwh: parseTurkishNumber(invoiceInput.unit_price),
          discount_pct: parseTurkishNumber(invoiceInput.discount_pct),
          distribution_unit_price_tl_per_kwh: parseTurkishNumber(invoiceInput.distribution_unit_price),
          declared_t1_kwh: parseTurkishNumber(invoiceInput.declared_t1_kwh),
          declared_t2_kwh: parseTurkishNumber(invoiceInput.declared_t2_kwh),
          declared_t3_kwh: parseTurkishNumber(invoiceInput.declared_t3_kwh),
          declared_total_kwh: parseTurkishNumber(invoiceInput.declared_total_kwh),
          declared_total_tl: parseTurkishNumber(invoiceInput.declared_total_tl),
        };
        requestBody = { invoices: [invoice] };
        invoicesForRender = [invoice];
      }

      const result = await analyzeRecon(file, requestBody);
      setReport(result);
      setSubmittedInvoices(invoicesForRender);
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

  const updateInvoiceField = (field: string, value: string) => {
    setInvoiceInput(prev => ({ ...prev, [field]: value }));
  };

  // ── Render ──

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-100 via-blue-50 to-violet-100 flex flex-col">
      {/* Header */}
      <header className="bg-white/70 backdrop-blur-md border-b border-indigo-200/60 flex-shrink-0 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-2.5">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-1.5 hover:bg-white/60 rounded-lg transition-colors"
              title="Geri"
            >
              <ArrowLeft className="w-5 h-5 text-indigo-700" />
            </button>
            <h1 className="text-lg font-bold bg-gradient-to-r from-blue-700 to-violet-700 bg-clip-text text-transparent">Fatura Mutabakat Analizi</h1>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl mx-auto px-4 py-2 w-full space-y-2">
        {/* File Upload Section */}
        <section className="bg-gradient-to-br from-blue-50 to-cyan-50 rounded-xl border border-blue-200/70 shadow-sm p-3">
          <h2 className="text-xs font-semibold text-blue-800 mb-1.5 uppercase tracking-wide">Excel Dosyası Yükle</h2>
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-2.5 text-center cursor-pointer transition-colors ${
              dragActive
                ? 'border-blue-500 bg-blue-100'
                : file
                ? 'border-emerald-400 bg-emerald-50'
                : 'border-blue-300 hover:border-blue-400 hover:bg-blue-50/60 bg-white/60'
            }`}
          >
            <div className="flex items-center justify-center gap-2">
              <Upload className={`w-4 h-4 ${file ? 'text-emerald-600' : 'text-blue-500'}`} />
              {file ? (
                <p className="text-sm text-emerald-700 font-medium">{file.name}</p>
              ) : (
                <p className="text-sm text-blue-700">Dosyayı sürükleyin veya tıklayın <span className="text-blue-400">(.xlsx / .xls, maks. 50 MB)</span></p>
              )}
            </div>
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
        <section className="bg-gradient-to-br from-violet-50 to-fuchsia-50 rounded-xl border border-violet-200/70 shadow-sm">
          <button
            onClick={() => setShowInvoiceInput(!showInvoiceInput)}
            className="w-full px-3 py-1.5 flex items-center justify-between text-left"
          >
            <span className="text-xs font-semibold text-violet-800 uppercase tracking-wide">Fatura Bilgileri (Opsiyonel)</span>
            {showInvoiceInput ? (
              <ChevronUp className="w-4 h-4 text-violet-600" />
            ) : (
              <ChevronDown className="w-4 h-4 text-violet-600" />
            )}
          </button>
          {showInvoiceInput && (
            <div className="px-3 pb-2 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Dönem</label>
                <input
                  type="month"
                  value={invoiceInput.period || ''}
                  onChange={(e) => updateInvoiceField('period', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Toplam fatura tutarı (TL)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 598.791,42"
                  value={invoiceInput.declared_total_tl}
                  onChange={(e) => updateInvoiceField('declared_total_tl', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
                <p className="mt-0.5 text-[10px] text-gray-500">Enerji bedeli toplamı (TL). Boş bırakılabilir.</p>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Birim Fiyat (TL/kWh)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 3,08"
                  value={invoiceInput.unit_price}
                  onChange={(e) => updateInvoiceField('unit_price', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">İndirim (%)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 4,77"
                  value={invoiceInput.discount_pct}
                  onChange={(e) => updateInvoiceField('discount_pct', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Dağıtım B.F. (TL/kWh)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 0,895372"
                  value={invoiceInput.distribution_unit_price}
                  onChange={(e) => updateInvoiceField('distribution_unit_price', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T1 kWh (Beyan)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 87.525,085"
                  value={invoiceInput.declared_t1_kwh}
                  onChange={(e) => updateInvoiceField('declared_t1_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T2 kWh (Beyan)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 41.397,409"
                  value={invoiceInput.declared_t2_kwh}
                  onChange={(e) => updateInvoiceField('declared_t2_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">T3 kWh (Beyan)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 65.490,353"
                  value={invoiceInput.declared_t3_kwh}
                  onChange={(e) => updateInvoiceField('declared_t3_kwh', e.target.value)}
                  className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Toplam kWh (Beyan)</label>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="örn. 194.412,847"
                  value={invoiceInput.declared_total_kwh}
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
          className="w-full py-2.5 bg-gradient-to-r from-blue-600 to-violet-600 text-white font-semibold rounded-lg hover:from-blue-700 hover:to-violet-700 disabled:from-slate-300 disabled:to-slate-300 disabled:cursor-not-allowed transition-all shadow-sm hover:shadow-md"
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
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
            <XCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {/* Results */}
        {report && (
          <ReconResults
            report={report}
            submittedInvoices={submittedInvoices}
          />
        )}
      </main>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Results Component (v2)
// ═══════════════════════════════════════════════════════════════════════════════

function ReconResults({
  report,
  submittedInvoices,
}: {
  report: ReconReport;
  submittedInvoices: InvoiceInput[];
}) {
  const isPartial = report.status === 'partial';
  const hasBlockedPeriods = report.periods.some(p => p.quote_blocked);

  // Lookup: period → declared_total_tl from the in-flight request body.
  const declaredTotalByPeriod = new Map<string, number | undefined>();
  for (const inv of submittedInvoices) {
    declaredTotalByPeriod.set(inv.period, inv.declared_total_tl);
  }

  return (
    <div className="space-y-2">
      {/* Status + Parse Stats — combined compact bar (kept from v1, amber-only) */}
      <div className={`rounded-lg px-3 py-2 flex items-center justify-between gap-4 flex-wrap shadow-sm ${
        isPartial && hasBlockedPeriods
          ? 'bg-gradient-to-r from-amber-100 to-orange-100 border border-amber-300'
          : 'bg-gradient-to-r from-emerald-100 to-teal-100 border border-emerald-300'
      }`}>
        <div className="flex items-center gap-2">
          {isPartial && hasBlockedPeriods ? (
            <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0" />
          ) : (
            <CheckCircle className="w-4 h-4 text-emerald-600 flex-shrink-0" />
          )}
          <p className={`text-sm font-semibold ${isPartial && hasBlockedPeriods ? 'text-amber-800' : 'text-emerald-800'}`}>
            {isPartial && hasBlockedPeriods ? 'Kısmi Sonuç' : 'Analiz Tamamlandı'}
          </p>
          <span className={`text-xs ${isPartial && hasBlockedPeriods ? 'text-amber-700' : 'text-emerald-700'}`}>
            · Format <span className="font-medium">{report.format_detected}</span> · {report.parse_stats.successful_rows}/{report.parse_stats.total_rows} satır
            {report.parse_stats.failed_rows > 0 && <span className="text-red-700"> · {report.parse_stats.failed_rows} hata</span>}
          </span>
        </div>
      </div>

      {/* Multi-period summary v2 — only renders when summary is non-null */}
      {report.summary && <MultiPeriodSummaryV2 summary={report.summary} />}

      {/* Period cards (v2) */}
      <div className="space-y-2">
        {report.periods.map((period: PeriodResult) => (
          <PeriodCardV2
            key={period.period}
            period={period}
            declaredTotalTl={declaredTotalByPeriod.get(period.period) ?? null}
          />
        ))}
      </div>

      {/* Warnings (v1, kept) */}
      {report.warnings.length > 0 && (
        <div className="bg-gradient-to-r from-amber-50 to-yellow-50 border border-amber-200 rounded-lg p-2">
          <h3 className="text-xs font-semibold text-amber-800 mb-1">Uyarılar</h3>
          <ul className="text-xs text-amber-700 space-y-0.5">
            {report.warnings.map((w, i) => (
              <li key={i}>• {w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
