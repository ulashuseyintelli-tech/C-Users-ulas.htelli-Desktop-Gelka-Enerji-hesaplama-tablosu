import { useState, useEffect, useCallback } from 'react';
import {
  ArrowLeft, BarChart3, Upload, Loader2, AlertCircle,
  Download, TrendingUp, TrendingDown,
} from 'lucide-react';
import {
  uploadConsumption, uploadMarketData, pricingAnalyze, pricingGetPeriods,
  pricingDownloadPdf, pricingDownloadExcel, customerSlug,
  type ConsumptionProfileSummary, type PricingAnalyzeResponse, type PricingAnalyzeRequest,
} from './api';

interface Props {
  onBack: () => void;
}

const fmt = (n: number, d = 2) =>
  (n ?? 0).toLocaleString('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt0 = (n: number) => (n ?? 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 });

/**
 * Tüketim Analizi Çalışma Alanı — firma saatlik tüketim Excel'i yükle, o dönem
 * için ağırlıklı PTF / PTF altı-üstü / worst hours analizi + PDF/Excel rapor.
 *
 * Çağrıldığı yer: App.tsx header "📊 Analiz" butonu → setShowConsumptionAnalysis(true).
 * Backend: /api/pricing/{upload-consumption,upload-market-data,analyze,periods,report/*}.
 */
export default function ConsumptionAnalysisPanel({ onBack }: Props) {
  // ── Firma + tüketim yükleme ──
  const [companyName, setCompanyName] = useState('');
  const customerId = customerSlug(companyName);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ConsumptionProfileSummary[]>([]);

  // ── Piyasa (EPİAŞ uzlaştırma) verisi ──
  const [marketPeriods, setMarketPeriods] = useState<string[]>([]);
  const [marketFile, setMarketFile] = useState<File | null>(null);
  const [marketUploading, setMarketUploading] = useState(false);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [showMarketUpload, setShowMarketUpload] = useState(false);

  // ── Analiz parametreleri / sonuç ──
  const [selectedPeriod, setSelectedPeriod] = useState('');
  const [multiplier, setMultiplier] = useState(1.05);
  const [voltage, setVoltage] = useState<'og' | 'ag'>('og');
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [result, setResult] = useState<PricingAnalyzeResponse | null>(null);
  const [reportLoading, setReportLoading] = useState<'pdf' | 'excel' | null>(null);

  const loadMarketPeriods = useCallback(async () => {
    try {
      const r = await pricingGetPeriods();
      setMarketPeriods(r.market_data_periods || []);
    } catch {
      /* fail-safe: liste alınamazsa boş kalır */
    }
  }, []);
  useEffect(() => { loadMarketPeriods(); }, [loadMarketPeriods]);

  const marketHas = (p: string) => marketPeriods.includes(p);

  const buildReq = (): PricingAnalyzeRequest => ({
    period: selectedPeriod,
    multiplier,
    customer_id: customerId,
    use_template: false,
    voltage_level: voltage,
  });

  const handleUpload = async () => {
    if (!file || !customerId) return;
    setUploading(true); setUploadError(null); setResult(null); setAnalyzeError(null);
    try {
      const res = await uploadConsumption(file, customerId, companyName);
      setProfiles(res.profiles);
      const withMarket = res.profiles.map((p) => p.period).filter(marketHas).sort();
      const def = withMarket.length
        ? withMarket[withMarket.length - 1]
        : (res.profiles[res.profiles.length - 1]?.period || '');
      setSelectedPeriod(def);
    } catch (e: any) {
      setUploadError(e?.message || 'Yükleme başarısız');
    } finally {
      setUploading(false);
    }
  };

  const handleMarketUpload = async () => {
    if (!marketFile) return;
    setMarketUploading(true); setMarketError(null);
    try {
      await uploadMarketData(marketFile);
      await loadMarketPeriods();
      setShowMarketUpload(false);
      setMarketFile(null);
      setAnalyzeError(null);
    } catch (e: any) {
      setMarketError(e?.message || 'Piyasa verisi yüklenemedi');
    } finally {
      setMarketUploading(false);
    }
  };

  const handleAnalyze = async () => {
    if (!selectedPeriod || !customerId) return;
    if (!marketHas(selectedPeriod)) {
      setShowMarketUpload(true);
      setAnalyzeError(`${selectedPeriod} dönemi için piyasa verisi yok. EPİAŞ uzlaştırma Excel'i yükleyin.`);
      return;
    }
    setAnalyzing(true); setAnalyzeError(null);
    try {
      setResult(await pricingAnalyze(buildReq()));
    } catch (e: any) {
      setAnalyzeError(e?.message || 'Analiz başarısız');
      setResult(null);
    } finally {
      setAnalyzing(false);
    }
  };

  const downloadBlob = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  };

  const handleReport = async (kind: 'pdf' | 'excel') => {
    if (!result) return;
    setReportLoading(kind);
    try {
      const req = buildReq();
      if (kind === 'pdf') {
        downloadBlob(await pricingDownloadPdf(req), `analiz_${customerId}_${selectedPeriod}.pdf`);
      } else {
        downloadBlob(await pricingDownloadExcel(req), `analiz_${customerId}_${selectedPeriod}.xlsx`);
      }
    } catch (e: any) {
      setAnalyzeError(e?.message || 'Rapor indirilemedi');
    } finally {
      setReportLoading(null);
    }
  };

  // PTF altı/üstü verdict: firma ağırlıklı PTF vs piyasa aritmetik ortalama
  const verdict = result
    ? (() => {
        const w = result.weighted_prices.weighted_ptf_tl_per_mwh;
        const a = result.weighted_prices.arithmetic_avg_ptf;
        const devPct = a ? (w / a - 1) * 100 : 0;
        return { w, a, devPct, above: w > a };
      })()
    : null;

  const periodOptions = profiles.map((p) => p.period);

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="p-2 hover:bg-gray-100 rounded-lg" aria-label="Geri">
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
              <BarChart3 className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">Tüketim Analizi</h1>
              <p className="text-sm text-gray-500">Firma saatlik tüketimi → ağırlıklı PTF / risk / rapor</p>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Sol: Yükleme + parametreler ── */}
        <section className="lg:col-span-1 space-y-4">
          {/* Firma + tüketim */}
          <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
            <h2 className="text-sm font-semibold text-gray-900">1. Firma Saatlik Tüketimi</h2>
            <div>
              <label className="text-xs font-medium text-gray-700 mb-1 block">Firma Adı</label>
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="Örn: Cansu Enerji"
                className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded outline-none focus:ring-1 focus:ring-primary-500"
              />
              {customerId && (
                <p className="text-[11px] text-gray-400 mt-0.5">id: <span className="font-mono">{customerId}</span></p>
              )}
            </div>
            <div>
              <label className="text-xs font-medium text-gray-700 mb-1 block">Tüketim Excel (.xlsx)</label>
              <input
                type="file"
                aria-label="Tüketim Excel"
                accept=".xlsx,.xls"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="w-full text-xs"
              />
            </div>
            <button
              onClick={handleUpload}
              disabled={!file || !customerId || uploading}
              className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-sm bg-primary-600 text-white rounded hover:bg-primary-700 disabled:opacity-50"
            >
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              Yükle
            </button>
            {uploadError && (
              <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5 flex gap-1">
                <AlertCircle className="w-4 h-4 flex-shrink-0" /> {uploadError}
              </div>
            )}
            {profiles.length > 0 && (
              <div className="text-xs space-y-1">
                <p className="font-medium text-gray-700">Yüklenen dönemler:</p>
                {profiles.map((p) => (
                  <div key={p.period} className="flex justify-between items-center text-gray-600">
                    <span>
                      {marketHas(p.period) ? '✅' : '⚠️'} <span className="font-mono">{p.period}</span>
                      {' '}· {fmt0(p.total_rows)} saat · {fmt0(p.total_kwh)} kWh
                    </span>
                    <span className="text-gray-400">k{p.quality_score}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Analiz parametreleri */}
          {profiles.length > 0 && (
            <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
              <h2 className="text-sm font-semibold text-gray-900">2. Analiz</h2>
              <div>
                <label className="text-xs font-medium text-gray-700 mb-1 block">Dönem</label>
                <select
                  aria-label="Dönem"
                  value={selectedPeriod}
                  onChange={(e) => { setSelectedPeriod(e.target.value); setResult(null); setAnalyzeError(null); }}
                  className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded outline-none focus:ring-1 focus:ring-primary-500"
                >
                  {periodOptions.map((p) => (
                    <option key={p} value={p}>{p} {marketHas(p) ? '(piyasa ✅)' : '(piyasa ⚠️ yok)'}</option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs font-medium text-gray-700 mb-1 block">Çarpan</label>
                  <input
                    type="number" step="0.01" min="1"
                    aria-label="Çarpan"
                    value={multiplier}
                    onChange={(e) => setMultiplier(parseFloat(e.target.value) || 1)}
                    className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded outline-none focus:ring-1 focus:ring-primary-500"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-700 mb-1 block">Gerilim</label>
                  <select
                    aria-label="Gerilim"
                    value={voltage}
                    onChange={(e) => setVoltage(e.target.value as 'og' | 'ag')}
                    className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded outline-none focus:ring-1 focus:ring-primary-500"
                  >
                    <option value="og">OG</option>
                    <option value="ag">AG</option>
                  </select>
                </div>
              </div>
              <button
                onClick={handleAnalyze}
                disabled={!selectedPeriod || analyzing}
                className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <BarChart3 className="w-4 h-4" />}
                Analiz Et
              </button>
              {analyzeError && (
                <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 flex gap-1">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" /> {analyzeError}
                </div>
              )}
            </div>
          )}

          {/* Piyasa verisi yükleme (koşullu-opsiyonel) */}
          {(showMarketUpload || (selectedPeriod && !marketHas(selectedPeriod))) && (
            <div className="bg-amber-50 rounded-lg border border-amber-200 p-4 space-y-2">
              <h2 className="text-sm font-semibold text-amber-900">EPİAŞ Piyasa Verisi Yükle</h2>
              <p className="text-xs text-amber-800">
                Seçili dönem için piyasa (uzlaştırma) verisi yok. Analiz için EPİAŞ uzlaştırma Excel'ini yükleyin.
              </p>
              <input
                type="file"
                aria-label="Piyasa Excel"
                accept=".xlsx,.xls"
                onChange={(e) => setMarketFile(e.target.files?.[0] || null)}
                className="w-full text-xs"
              />
              <button
                onClick={handleMarketUpload}
                disabled={!marketFile || marketUploading}
                className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-sm bg-amber-600 text-white rounded hover:bg-amber-700 disabled:opacity-50"
              >
                {marketUploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                Piyasa Verisi Yükle
              </button>
              {marketError && (
                <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">{marketError}</div>
              )}
            </div>
          )}
        </section>

        {/* ── Sağ: Sonuç ── */}
        <section className="lg:col-span-2">
          {!result ? (
            <div className="bg-white rounded-lg border border-dashed border-gray-300 p-10 text-center text-gray-400 text-sm">
              Firma tüketim Excel'ini yükleyip dönem seçerek analiz başlatın.
            </div>
          ) : (
            <div className="space-y-4">
              {/* PTF altı/üstü verdict */}
              {verdict && (
                <div className={`rounded-lg border p-4 ${verdict.above ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-200'}`}>
                  <div className="flex items-center gap-2">
                    {verdict.above
                      ? <TrendingUp className="w-6 h-6 text-red-600" />
                      : <TrendingDown className="w-6 h-6 text-green-600" />}
                    <div>
                      <p className={`text-sm font-bold ${verdict.above ? 'text-red-800' : 'text-green-800'}`}>
                        Firma ağırlıklı PTF, piyasa ortalamasının{' '}
                        <span data-testid="verdict-direction">{verdict.above ? 'ÜSTÜNDE' : 'ALTINDA'}</span>
                        {' '}(%{fmt(Math.abs(verdict.devPct), 1)})
                      </p>
                      <p className="text-xs text-gray-600">
                        Ağırlıklı: <span className="font-mono" data-testid="weighted-ptf">{fmt(verdict.w)}</span> TL/MWh ·
                        Piyasa ort.: <span className="font-mono">{fmt(verdict.a)}</span> TL/MWh
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Özet metrikler */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <p className="text-xs text-gray-500">Toplam Tüketim</p>
                  <p className="text-sm font-bold text-gray-900">{fmt0(result.weighted_prices.total_consumption_kwh)} kWh</p>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <p className="text-xs text-gray-500">Tedarik Maliyeti</p>
                  <p className="text-sm font-bold text-gray-900">{fmt(result.supplier_cost.total_cost_tl_per_mwh)} TL/MWh</p>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <p className="text-xs text-gray-500">Net Marj</p>
                  <p className={`text-sm font-bold ${result.pricing.net_margin_tl_per_mwh < 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {fmt(result.pricing.net_margin_tl_per_mwh)} TL/MWh
                  </p>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <p className="text-xs text-gray-500">Satış-altı Saat</p>
                  <p className="text-sm font-bold text-gray-900">
                    {result.loss_map.total_loss_hours} / {result.weighted_prices.hours_count}
                  </p>
                </div>
              </div>

              {/* Risk flags */}
              {result.pricing.risk_flags && result.pricing.risk_flags.length > 0 && (
                <div className="space-y-1">
                  {result.pricing.risk_flags.map((f, i) => (
                    <div key={i} className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5 flex gap-1">
                      <AlertCircle className="w-4 h-4 flex-shrink-0" /> {f.message}
                    </div>
                  ))}
                </div>
              )}

              {/* Uyarılar (YEKDEM vb.) */}
              {result.warnings && result.warnings.length > 0 && (
                <div className="space-y-1">
                  {result.warnings.map((w, i) => (
                    <div key={i} className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">{w.message}</div>
                  ))}
                </div>
              )}

              {/* T1/T2/T3 dilim kırılımı */}
              {result.time_zone_breakdown && (
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">Zaman Dilimi Kırılımı</h3>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    {(['T1', 'T2', 'T3'] as const).map((z) => {
                      const tz = result.time_zone_breakdown?.[z];
                      if (!tz) return null;
                      return (
                        <div key={z} className="bg-gray-50 rounded p-2">
                          <p className="font-medium text-gray-700">{tz.label}</p>
                          <p className="text-gray-600">{fmt0(tz.consumption_kwh)} kWh (%{fmt(tz.consumption_pct, 1)})</p>
                          <p className="text-gray-500">PTF {fmt(tz.weighted_ptf_tl_per_mwh)}</p>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Worst hours */}
              {result.loss_map.worst_hours && result.loss_map.worst_hours.length > 0 && (
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">En Kötü Saatler (satış &lt; PTF)</h3>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 text-left">
                        <th className="py-1">Tarih</th><th>Saat</th><th className="text-right">PTF</th>
                        <th className="text-right">Satış</th><th className="text-right">Zarar</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.loss_map.worst_hours.slice(0, 8).map((h, i) => (
                        <tr key={i} className="border-t border-gray-100">
                          <td className="py-1 font-mono">{h.date}</td>
                          <td>{String(h.hour).padStart(2, '0')}:00</td>
                          <td className="text-right">{fmt(h.ptf)}</td>
                          <td className="text-right">{fmt(h.sales_price)}</td>
                          <td className="text-right text-red-600">{fmt(h.loss_tl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Rapor indir */}
              <div className="flex gap-2">
                <button
                  onClick={() => handleReport('pdf')}
                  disabled={reportLoading !== null}
                  className="flex items-center gap-2 px-4 py-2 text-sm bg-primary-600 text-white rounded hover:bg-primary-700 disabled:opacity-50"
                >
                  {reportLoading === 'pdf' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                  PDF Rapor
                </button>
                <button
                  onClick={() => handleReport('excel')}
                  disabled={reportLoading !== null}
                  className="flex items-center gap-2 px-4 py-2 text-sm bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
                >
                  {reportLoading === 'excel' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                  Excel Rapor
                </button>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
