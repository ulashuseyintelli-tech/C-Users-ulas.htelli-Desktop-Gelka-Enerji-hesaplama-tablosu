import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { ArrowLeft, Upload, Loader2, CheckCircle2, XCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import { uploadMarketData, getMarketDataPeriods, MarketDataApiError, UploadMarketDataResponse } from './marketDataApi';

// ═══════════════════════════════════════════════════════════════════════════════
// EPİAŞ Piyasa Verisi Yönetimi — Excel Yükleme & Kapsam Tablosu
//
// Fatura Mutabakat Analizi (recon/ReconPage.tsx) ile KARIŞTIRILMASIN: o ekran
// tek bir müşterinin faturasını analiz eder. Bu sayfa TÜM sistemin ortak
// kullandığı, tenant'sız piyasa referans verisini (hourly_market_prices —
// saatlik PTF/SMF) yönetir; EPİAŞ'ın "Uzlaştırma Dönemi Detayı" export'unu
// yükler ve hangi ayların dolu/eksik olduğunu gösterir.
//
// Backend YENİ DEĞİL (bkz. marketDataApi.ts) — bu sayfa var olan
// POST /api/pricing/upload-market-data + GET /api/pricing/periods
// endpoint'lerine önceden hiç bağlanmamış bir arayüz ekler.
//
// Çoklu dosya: backend tek seferde bir dosya kabul ediyor, bu yüzden birden
// çok ay seçildiğinde dosyalar SIRAYLA yüklenir (owner isteği: "hepsi
// eksikse hepsini yükle").
// ═══════════════════════════════════════════════════════════════════════════════

interface MarketDataPageProps {
  onBack: () => void;
}

type FileStatus = 'pending' | 'uploading' | 'done' | 'error';

interface QueuedFile {
  file: File;
  status: FileStatus;
  result?: UploadMarketDataResponse;
  error?: string;
}

const LOOKBACK_MONTHS = 12;

/** Son LOOKBACK_MONTHS ayı (bu ay dahil), en yeniden en eskiye sıralı YYYY-MM listesi olarak üretir. */
function buildRecentMonths(count: number): string[] {
  const months: string[] = [];
  const now = new Date();
  for (let i = 0; i < count; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }
  return months;
}

export default function MarketDataPage({ onBack }: MarketDataPageProps) {
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [loadedPeriods, setLoadedPeriods] = useState<string[] | null>(null);
  const [periodsError, setPeriodsError] = useState<string | null>(null);
  const [periodsLoading, setPeriodsLoading] = useState(false);

  const recentMonths = useMemo(() => buildRecentMonths(LOOKBACK_MONTHS), []);

  const refreshPeriods = useCallback(async () => {
    setPeriodsLoading(true);
    setPeriodsError(null);
    try {
      const data = await getMarketDataPeriods();
      setLoadedPeriods(data.market_data_periods || []);
    } catch (err: any) {
      setPeriodsError(err?.message || 'Dönem listesi alınamadı.');
    } finally {
      setPeriodsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshPeriods();
  }, [refreshPeriods]);

  const loadedSet = useMemo(() => new Set(loadedPeriods || []), [loadedPeriods]);
  const missingRecentMonths = useMemo(
    () => recentMonths.filter((m) => !loadedSet.has(m)),
    [recentMonths, loadedSet]
  );

  // ── Dosya seçimi ──

  const addFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files);
    setQueue((prev) => [
      ...prev,
      ...arr.map((file) => ({ file, status: 'pending' as FileStatus })),
    ]);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }, [addFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback(() => setDragActive(false), []);

  const removeFromQueue = (idx: number) => {
    setQueue((prev) => prev.filter((_, i) => i !== idx));
  };

  const clearFinished = () => {
    setQueue((prev) => prev.filter((q) => q.status === 'pending'));
  };

  // ── Sırayla yükleme ──

  const handleUploadAll = useCallback(async () => {
    setUploading(true);
    // Kapanışta stale closure olmasın diye index bazlı, tek tek güncelleme.
    for (let i = 0; i < queue.length; i++) {
      if (queue[i].status !== 'pending') continue;
      setQueue((prev) => prev.map((q, idx) => (idx === i ? { ...q, status: 'uploading' } : q)));
      try {
        const result = await uploadMarketData(queue[i].file);
        setQueue((prev) => prev.map((q, idx) => (idx === i ? { ...q, status: 'done', result } : q)));
      } catch (err) {
        const message = err instanceof MarketDataApiError ? err.message : 'Beklenmeyen bir hata oluştu.';
        setQueue((prev) => prev.map((q, idx) => (idx === i ? { ...q, status: 'error', error: message } : q)));
      }
    }
    setUploading(false);
    refreshPeriods();
  }, [queue, refreshPeriods]);

  const pendingCount = queue.filter((q) => q.status === 'pending').length;

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-100 via-blue-50 to-violet-100 flex flex-col">
      {/* Header */}
      <header className="bg-white/70 backdrop-blur-md border-b border-indigo-200/60 flex-shrink-0 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-2.5">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="p-1.5 hover:bg-white/60 rounded-lg transition-colors" title="Geri">
              <ArrowLeft className="w-5 h-5 text-indigo-700" />
            </button>
            <h1 className="text-lg font-bold bg-gradient-to-r from-blue-700 to-violet-700 bg-clip-text text-transparent">
              EPİAŞ Piyasa Verisi
            </h1>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl mx-auto px-4 py-3 w-full space-y-3">
        {/* Kapsam Tablosu */}
        <section className="bg-white/80 rounded-xl border border-indigo-200/60 shadow-sm p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-xs font-semibold text-indigo-800 uppercase tracking-wide">
              Son {LOOKBACK_MONTHS} Ay — Saatlik PTF/SMF Kapsamı
            </h2>
            <button
              onClick={refreshPeriods}
              disabled={periodsLoading}
              className="p-1 hover:bg-indigo-50 rounded transition-colors disabled:opacity-50"
              title="Yenile"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-indigo-500 ${periodsLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {periodsError && (
            <p className="text-sm text-red-600 mb-2">{periodsError}</p>
          )}

          {loadedPeriods === null && !periodsError ? (
            <div className="text-sm text-gray-500 flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Kapsam yükleniyor...
            </div>
          ) : (
            <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-12 gap-1.5">
              {recentMonths.slice().reverse().map((m) => {
                const loaded = loadedSet.has(m);
                return (
                  <div
                    key={m}
                    className={`rounded-md border px-1.5 py-1.5 text-center text-xs font-medium ${
                      loaded
                        ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                        : 'border-red-200 bg-red-50 text-red-600'
                    }`}
                    title={loaded ? `${m}: veri yüklü` : `${m}: veri eksik`}
                  >
                    <div>{m}</div>
                    {loaded ? (
                      <CheckCircle2 className="w-3.5 h-3.5 mx-auto mt-0.5" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5 mx-auto mt-0.5" />
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {loadedPeriods !== null && missingRecentMonths.length > 0 && (
            <p className="text-xs text-amber-700 mt-2">
              Eksik aylar: {missingRecentMonths.slice().reverse().join(', ')}
            </p>
          )}
        </section>

        {/* Dosya Yükleme */}
        <section className="bg-gradient-to-br from-blue-50 to-cyan-50 rounded-xl border border-blue-200/70 shadow-sm p-3">
          <h2 className="text-xs font-semibold text-blue-800 mb-1.5 uppercase tracking-wide">
            EPİAŞ Uzlaştırma Dönemi Detayı — Excel Yükle
          </h2>
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-2.5 text-center cursor-pointer transition-colors ${
              dragActive
                ? 'border-blue-500 bg-blue-100'
                : 'border-blue-300 hover:border-blue-400 hover:bg-blue-50/60 bg-white/60'
            }`}
          >
            <div className="flex items-center justify-center gap-2">
              <Upload className="w-4 h-4 text-blue-500" />
              <p className="text-sm text-blue-700">
                Bir veya birden çok dosyayı sürükleyin veya tıklayın{' '}
                <span className="text-blue-400">(.xlsx / .xls, dosya başına maks. 50 MB)</span>
              </p>
            </div>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = '';
            }}
          />

          {queue.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {queue.map((q, idx) => (
                <div
                  key={idx}
                  className={`flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
                    q.status === 'error'
                      ? 'border-red-200 bg-red-50'
                      : q.status === 'done'
                      ? 'border-emerald-200 bg-emerald-50'
                      : 'border-gray-200 bg-white'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {q.status === 'uploading' && <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500 flex-shrink-0" />}
                    {q.status === 'done' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 flex-shrink-0" />}
                    {q.status === 'error' && <XCircle className="w-3.5 h-3.5 text-red-600 flex-shrink-0" />}
                    {q.status === 'pending' && <div className="w-3.5 h-3.5 flex-shrink-0" />}
                    <span className="truncate text-gray-800">{q.file.name}</span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {q.status === 'done' && q.result && (
                      <span className="text-emerald-700">
                        {q.result.period} · {q.result.total_rows}/{q.result.expected_hours} satır · kalite {q.result.quality_score}
                        {q.result.previous_version_archived ? ' · önceki versiyon arşivlendi' : ''}
                      </span>
                    )}
                    {q.status === 'error' && <span className="text-red-700">{q.error}</span>}
                    {q.status === 'pending' && (
                      <button onClick={() => removeFromQueue(idx)} className="text-gray-400 hover:text-red-500">
                        <XCircle className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))}

              {queue.some((q) => q.result && q.result.warnings?.length > 0) && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <div>
                    {queue
                      .filter((q) => q.result && q.result.warnings?.length > 0)
                      .map((q, i) => (
                        <div key={i}>
                          {q.file.name}: {q.result!.warnings.join(' | ')}
                        </div>
                      ))}
                  </div>
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                <button
                  onClick={handleUploadAll}
                  disabled={uploading || pendingCount === 0}
                  className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1.5 disabled:opacity-50"
                >
                  {uploading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {pendingCount > 0 ? `${pendingCount} dosyayı yükle` : 'Yüklenecek dosya yok'}
                </button>
                {queue.some((q) => q.status === 'done' || q.status === 'error') && (
                  <button onClick={clearFinished} className="btn-secondary text-xs px-3 py-1.5">
                    Tamamlananları temizle
                  </button>
                )}
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
