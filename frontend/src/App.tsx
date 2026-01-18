import { useState, useCallback, useMemo } from 'react';
import { Upload, FileText, Zap, TrendingDown, AlertCircle, CheckCircle, Loader2, RefreshCw, Download, Settings } from 'lucide-react';
import { fullProcess, generateOfferPdf, FullProcessResponse } from './api';
import AdminPanel from './AdminPanel';

// EPDK Dağıtım Tarifeleri (Ocak 2025)
const DISTRIBUTION_TARIFFS = [
  { key: 'sanayi_og_cift', label: 'Sanayi OG Çift Terim', price: 0.810595 },
  { key: 'sanayi_og_tek', label: 'Sanayi OG Tek Terim', price: 0.895372 },
  { key: 'sanayi_ag_tek', label: 'Sanayi AG Tek Terim', price: 1.385324 },
  { key: 'kamu_og_cift', label: 'Kamu/Özel OG Çift Terim', price: 1.263293 },
  { key: 'kamu_og_tek', label: 'Kamu/Özel OG Tek Terim', price: 1.57581 },
  { key: 'kamu_ag_tek', label: 'Kamu/Özel AG Tek Terim', price: 1.87741 },
  { key: 'custom', label: 'Manuel Giriş', price: 0 },
];

function App() {
  // Admin panel state
  const [showAdminPanel, setShowAdminPanel] = useState(false);
  
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<FullProcessResponse | null>(null);
  const [dragActive, setDragActive] = useState(false);
  
  // Teklif parametreleri
  const [ptfPrice, setPtfPrice] = useState(2974.1);
  const [yekdemPrice, setYekdemPrice] = useState(364.0);
  const [multiplier, setMultiplier] = useState(1.01);
  
  // PTF/YEKDEM kaynağı: true = DB'den otomatik, false = manuel override
  const [useReferencePrices, setUseReferencePrices] = useState(true);
  
  // Dağıtım birim fiyatı (manuel override)
  const [distributionTariffKey, setDistributionTariffKey] = useState<string>('');
  const [customDistributionPrice, setCustomDistributionPrice] = useState<number>(0);
  
  // Çarpan seçenekleri
  const multiplierOptions = [
    { value: 1.01, label: '1.01 (%1 kar)' },
    { value: 1.02, label: '1.02 (%2 kar)' },
    { value: 1.03, label: '1.03 (%3 kar)' },
    { value: 1.05, label: '1.05 (%5 kar)' },
    { value: 1.10, label: '1.10 (%10 kar)' },
    { value: 1.15, label: '1.15 (%15 kar)' },
  ];
  
  // Dağıtım birim fiyatını belirle (öncelik: manuel > tarife seçimi > backend)
  const getDistributionUnitPrice = useCallback(() => {
    if (distributionTariffKey === 'custom' && customDistributionPrice > 0) {
      return customDistributionPrice;
    }
    if (distributionTariffKey && distributionTariffKey !== 'custom') {
      const tariff = DISTRIBUTION_TARIFFS.find(t => t.key === distributionTariffKey);
      if (tariff) return tariff.price;
    }
    // Backend'den gelen değer
    return result?.extraction?.distribution_unit_price_tl_per_kwh?.value || 0;
  }, [distributionTariffKey, customDistributionPrice, result?.extraction?.distribution_unit_price_tl_per_kwh?.value]);
  
  // Parametreler değiştiğinde otomatik yeniden hesaplama
  // Backend'den gelen calculation varsa onu kullan, yoksa frontend'de hesapla
  const liveCalculation = useMemo(() => {
    if (!result?.extraction) return null;
    
    // Backend'den gelen calculation'ı temel al
    const backendCalc = result.calculation;
    
    // Eğer parametreler değiştiyse frontend'de yeniden hesapla
    // Ama mevcut fatura değerleri her zaman backend'den gelsin (faturadan okunan gerçek değerler)
    const kwh = result.extraction.consumption_kwh?.value || 0;
    const distUnitPrice = getDistributionUnitPrice();
    
    const ptfKwh = ptfPrice / 1000;
    const yekdemKwh = yekdemPrice / 1000;
    
    // Mevcut fatura değerleri: Backend'den gelen gerçek değerler (faturadan okunan)
    const current_energy_tl = backendCalc?.current_energy_tl || 0;
    // Mevcut dağıtım: Backend'den gelen veya manuel override ile hesapla
    const backendDistUnitPrice = result.extraction.distribution_unit_price_tl_per_kwh?.value || 0;
    const current_distribution_tl = backendCalc?.current_distribution_tl || (kwh * backendDistUnitPrice);
    const current_btv_tl = backendCalc?.current_btv_tl || 0;
    const current_vat_matrah_tl = backendCalc?.current_vat_matrah_tl || 0;
    const current_vat_tl = backendCalc?.current_vat_tl || 0;
    const current_total_with_vat_tl = backendCalc?.current_total_with_vat_tl || 0;
    
    // YEKDEM: Backend'in kararını kullan (faturada YEKDEM varsa dahil et, yoksa etme)
    // meta_include_yekdem_in_offer backend tarafından faturaya göre belirleniyor
    const includeYekdem = backendCalc?.meta_include_yekdem_in_offer || false;
    
    // Teklif fatura: Parametrelere göre frontend'de hesapla
    // YEKDEM sadece faturada varsa dahil edilir
    const offerBasePrice = includeYekdem ? (ptfKwh + yekdemKwh) : ptfKwh;
    
    // ÖNEMLİ: Excel mantığı - önce enerji bedeli, sonra marj
    // ❌ Yanlış: birim_fiyat = PTF × marj, enerji = kWh × birim_fiyat
    // ✅ Doğru: enerji_base = kWh × PTF, enerji = enerji_base × marj
    const offer_energy_base = kwh * offerBasePrice;
    const offer_energy_tl = offer_energy_base * multiplier;
    
    const offer_distribution_tl = kwh * distUnitPrice;
    const offer_btv_tl = offer_energy_tl * 0.01;
    const offer_vat_matrah_tl = offer_energy_tl + offer_distribution_tl + offer_btv_tl;
    const offer_vat_tl = offer_vat_matrah_tl * 0.20;
    const offer_total_with_vat_tl = offer_vat_matrah_tl + offer_vat_tl;
    
    // Fark ve tasarruf
    const difference_excl_vat_tl = current_vat_matrah_tl - offer_vat_matrah_tl;
    const difference_incl_vat_tl = current_total_with_vat_tl - offer_total_with_vat_tl;
    const savings_ratio = current_total_with_vat_tl > 0 ? difference_incl_vat_tl / current_total_with_vat_tl : 0;
    
    // Tedarikçi karı (marjdan gelen kar)
    const supplier_profit_tl = offer_energy_base * (multiplier - 1);
    const supplier_profit_margin = (multiplier - 1) * 100;
    
    return {
      current_energy_tl,
      current_distribution_tl,
      current_btv_tl,
      current_vat_matrah_tl,
      current_vat_tl,
      current_total_with_vat_tl,
      offer_energy_tl,
      offer_distribution_tl,
      offer_btv_tl,
      offer_vat_matrah_tl,
      offer_vat_tl,
      offer_total_with_vat_tl,
      difference_excl_vat_tl,
      difference_incl_vat_tl,
      savings_ratio,
      supplier_profit_tl,
      supplier_profit_margin,
      include_yekdem: includeYekdem,  // UI'da göstermek için
      distribution_unit_price: distUnitPrice,  // UI'da göstermek için
    };
  }, [result?.extraction, result?.calculation, ptfPrice, yekdemPrice, multiplier, getDistributionUnitPrice]);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile.type === 'application/pdf' || droppedFile.type.startsWith('image/')) {
        setFile(droppedFile);
        setError(null);
        setResult(null);
      } else {
        setError('Sadece PDF veya resim dosyaları kabul edilir.');
      }
    }
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
      setResult(null);
    }
  };

  const handleAnalyze = async () => {
    if (!file) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fullProcess(file, {
        weighted_ptf_tl_per_mwh: ptfPrice,
        yekdem_tl_per_mwh: yekdemPrice,
        agreement_multiplier: multiplier,
        use_reference_prices: useReferencePrices,
      });
      
      // Hesaplama hatası varsa göster
      if (response.calculation_error) {
        setError(`Hesaplama hatası: ${response.calculation_error}`);
        setResult(response);
        return;
      }
      
      // Check if calculation is available
      if (!response.calculation) {
        const missingFields = response.validation?.missing_fields || [];
        const errors = response.validation?.errors || [];
        // errors array'i object içerebilir, string'e çevir
        const errorStrings = errors.map((e: any) => typeof e === 'string' ? e : JSON.stringify(e));
        const allIssues = [...missingFields, ...errorStrings];
        setError(`Fatura analizi tamamlandı ancak hesaplama yapılamadı. ${allIssues.length > 0 ? 'Eksik/hatalı alanlar: ' + allIssues.join(', ') : 'Lütfen faturayı kontrol edin.'}`);
        setResult(response);
        return;
      }
      
      // Backend'den dönen PTF/YEKDEM değerlerini auto-fill yap (referans modunda)
      if (useReferencePrices && response.calculation) {
        const backendPtf = response.calculation.meta_ptf_tl_per_mwh;
        const backendYekdem = response.calculation.meta_yekdem_tl_per_mwh;
        if (backendPtf && backendPtf > 0) {
          setPtfPrice(backendPtf);
        }
        if (backendYekdem !== undefined) {
          setYekdemPrice(backendYekdem);
        }
      }
      
      setResult(response);
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || err.message || 'Analiz sırasında bir hata oluştu.';
      setError(typeof errorMsg === 'string' ? errorMsg : JSON.stringify(errorMsg));
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setResult(null);
    setError(null);
  };

  const handleDownloadPdf = async () => {
    if (!result?.extraction || !liveCalculation) return;
    
    setPdfLoading(true);
    try {
      const pdfBlob = await generateOfferPdf(
        result.extraction,
        {
          // liveCalculation'dan güncel değerleri gönder
          current_energy_tl: liveCalculation.current_energy_tl,
          current_distribution_tl: liveCalculation.current_distribution_tl,
          current_btv_tl: liveCalculation.current_btv_tl,
          current_vat_matrah_tl: liveCalculation.current_vat_matrah_tl,
          current_vat_tl: liveCalculation.current_vat_tl,
          current_total_with_vat_tl: liveCalculation.current_total_with_vat_tl,
          offer_energy_tl: liveCalculation.offer_energy_tl,
          offer_distribution_tl: liveCalculation.offer_distribution_tl,
          offer_btv_tl: liveCalculation.offer_btv_tl,
          offer_vat_matrah_tl: liveCalculation.offer_vat_matrah_tl,
          offer_vat_tl: liveCalculation.offer_vat_tl,
          offer_total_with_vat_tl: liveCalculation.offer_total_with_vat_tl,
          difference_excl_vat_tl: liveCalculation.difference_excl_vat_tl,
          difference_incl_vat_tl: liveCalculation.difference_incl_vat_tl,
          savings_ratio: liveCalculation.savings_ratio,
          meta_include_yekdem_in_offer: liveCalculation.include_yekdem,
        },
        {
          weighted_ptf_tl_per_mwh: ptfPrice,
          yekdem_tl_per_mwh: liveCalculation.include_yekdem ? yekdemPrice : 0,
          agreement_multiplier: multiplier,
        }
      );
      
      // Download the PDF
      const url = window.URL.createObjectURL(pdfBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `teklif_${result.extraction.invoice_period || 'fatura'}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || err.message || 'PDF oluşturulurken hata oluştu.';
      setError(typeof errorMsg === 'string' ? errorMsg : JSON.stringify(errorMsg));
    } finally {
      setPdfLoading(false);
    }
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('tr-TR', {
      style: 'currency',
      currency: 'TRY',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const formatPercent = (value: number) => {
    return new Intl.NumberFormat('tr-TR', {
      style: 'percent',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  // Admin panel göster
  if (showAdminPanel) {
    return <AdminPanel onBack={() => setShowAdminPanel(false)} />;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
                <Zap className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">Gelka Enerji</h1>
                <p className="text-sm text-gray-500">Fatura Analiz ve Teklif Sistemi</p>
              </div>
            </div>
            <button
              onClick={() => setShowAdminPanel(true)}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              title="Admin Panel"
            >
              <Settings className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Sol Panel - Yükleme ve Parametreler */}
          <div className="lg:col-span-1 space-y-6">
            {/* Dosya Yükleme */}
            <div className="card">
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Upload className="w-5 h-5 text-primary-600" />
                Fatura Yükle
              </h2>
              
              <div
                className={`border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
                  dragActive
                    ? 'border-primary-500 bg-primary-50'
                    : file
                    ? 'border-primary-300 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
              >
                {file ? (
                  <div className="space-y-3">
                    <FileText className="w-12 h-12 text-primary-600 mx-auto" />
                    <p className="font-medium text-gray-900">{file.name}</p>
                    <p className="text-sm text-gray-500">
                      {(file.size / 1024 / 1024).toFixed(2)} MB
                    </p>
                    <button
                      onClick={handleReset}
                      className="text-sm text-primary-600 hover:text-primary-700 font-medium"
                    >
                      Değiştir
                    </button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <Upload className="w-12 h-12 text-gray-400 mx-auto" />
                    <div>
                      <p className="text-gray-600">
                        Faturayı sürükleyip bırakın veya
                      </p>
                      <label className="text-primary-600 hover:text-primary-700 font-medium cursor-pointer">
                        dosya seçin
                        <input
                          type="file"
                          className="hidden"
                          accept=".pdf,.html,.htm,image/*,text/html"
                          onChange={handleFileChange}
                        />
                      </label>
                    </div>
                    <p className="text-xs text-gray-400">PDF veya resim dosyası</p>
                  </div>
                )}
              </div>
            </div>

            {/* Teklif Parametreleri */}
            <div className="card">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">
                Teklif Parametreleri
              </h2>
              
              <div className="space-y-4">
                {/* PTF/YEKDEM Kaynak Seçimi */}
                <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-gray-700">PTF/YEKDEM Kaynağı</span>
                    <button
                      type="button"
                      onClick={() => setUseReferencePrices(!useReferencePrices)}
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                        useReferencePrices ? 'bg-primary-600' : 'bg-gray-300'
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                          useReferencePrices ? 'translate-x-6' : 'translate-x-1'
                        }`}
                      />
                    </button>
                  </div>
                  <p className="text-xs text-gray-500">
                    {useReferencePrices ? (
                      <span className="text-primary-600 font-medium">Otomatik: Fatura dönemine göre DB'den çekilir</span>
                    ) : (
                      <span className="text-amber-600 font-medium">Manuel: Aşağıdaki değerler kullanılır</span>
                    )}
                  </p>
                  {/* Kaynak Badge */}
                  {result?.calculation?.meta_pricing_source && (
                    <div className="mt-2 flex items-center gap-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        result.calculation.meta_pricing_source === 'reference' 
                          ? 'bg-primary-100 text-primary-700'
                          : result.calculation.meta_pricing_source === 'override'
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-gray-100 text-gray-700'
                      }`}>
                        {result.calculation.meta_pricing_source === 'reference' && '📊 Referans'}
                        {result.calculation.meta_pricing_source === 'override' && '✏️ Override'}
                        {result.calculation.meta_pricing_source === 'default' && '⚠️ Default'}
                      </span>
                      {result.calculation.meta_pricing_period && (
                        <span className="text-xs text-gray-500">
                          Dönem: {result.calculation.meta_pricing_period}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                
                <div>
                  <label className="label">
                    PTF Fiyatı (TL/MWh)
                    {useReferencePrices && result?.calculation?.meta_ptf_tl_per_mwh && (
                      <span className="text-xs text-primary-600 ml-2">(DB'den: {result.calculation.meta_ptf_tl_per_mwh})</span>
                    )}
                  </label>
                  <input
                    type="number"
                    className={`input ${useReferencePrices ? 'bg-gray-50' : ''}`}
                    value={ptfPrice}
                    onChange={(e) => setPtfPrice(parseFloat(e.target.value) || 0)}
                    step="0.1"
                    disabled={useReferencePrices}
                  />
                </div>
                
                <div>
                  <label className="label">
                    YEKDEM Fiyatı (TL/MWh)
                    {liveCalculation && !liveCalculation.include_yekdem && (
                      <span className="text-xs text-gray-500 ml-2">(Faturada YEKDEM yok)</span>
                    )}
                    {useReferencePrices && result?.calculation?.meta_yekdem_tl_per_mwh !== undefined && (
                      <span className="text-xs text-primary-600 ml-2">(DB'den: {result.calculation.meta_yekdem_tl_per_mwh})</span>
                    )}
                  </label>
                  <input
                    type="number"
                    className={`input ${(liveCalculation && !liveCalculation.include_yekdem) || useReferencePrices ? 'bg-gray-100 text-gray-400' : ''}`}
                    value={liveCalculation && !liveCalculation.include_yekdem ? 0 : yekdemPrice}
                    onChange={(e) => setYekdemPrice(parseFloat(e.target.value) || 0)}
                    step="0.1"
                    disabled={!!(liveCalculation && !liveCalculation.include_yekdem) || useReferencePrices}
                  />
                </div>
                
                <div>
                  <label className="label">Anlaşma Çarpanı (Kar Marjı)</label>
                  <input
                    type="number"
                    className="input"
                    value={multiplier}
                    onChange={(e) => setMultiplier(parseFloat(e.target.value) || 1)}
                    step="0.01"
                    min="1"
                    max="2"
                  />
                  <div className="flex flex-wrap gap-1 mt-2">
                    {multiplierOptions.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setMultiplier(opt.value)}
                        className={`px-2 py-1 text-xs rounded-md transition-colors ${
                          multiplier === opt.value
                            ? 'bg-primary-600 text-white'
                            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                        }`}
                      >
                        {opt.value}
                      </button>
                    ))}
                  </div>
                </div>
                
                {/* Dağıtım Bedeli Tarife Seçimi */}
                <div className="pt-4 border-t border-gray-200">
                  <label className="label">Dağıtım Bedeli (EPDK Tarifesi)</label>
                  <select
                    className="input"
                    value={distributionTariffKey}
                    onChange={(e) => {
                      setDistributionTariffKey(e.target.value);
                      if (e.target.value !== 'custom') {
                        setCustomDistributionPrice(0);
                      }
                    }}
                  >
                    <option value="">Faturadan Oku (Otomatik)</option>
                    {DISTRIBUTION_TARIFFS.map((tariff) => (
                      <option key={tariff.key} value={tariff.key}>
                        {tariff.label} {tariff.key !== 'custom' ? `(${tariff.price.toFixed(6)} TL/kWh)` : ''}
                      </option>
                    ))}
                  </select>
                  
                  {/* Manuel Giriş Alanı */}
                  {distributionTariffKey === 'custom' && (
                    <div className="mt-2">
                      <input
                        type="number"
                        className="input"
                        placeholder="Dağıtım birim fiyatı (TL/kWh)"
                        value={customDistributionPrice || ''}
                        onChange={(e) => setCustomDistributionPrice(parseFloat(e.target.value) || 0)}
                        step="0.000001"
                        min="0"
                      />
                    </div>
                  )}
                  
                  {/* Seçilen Dağıtım Fiyatı Gösterimi */}
                  {liveCalculation && (
                    <p className="text-xs text-gray-500 mt-2">
                      Kullanılan: <span className="font-medium text-gray-700">{liveCalculation.distribution_unit_price.toFixed(6)} TL/kWh</span>
                      {distributionTariffKey && distributionTariffKey !== 'custom' && (
                        <span className="ml-1 text-primary-600">(EPDK)</span>
                      )}
                      {distributionTariffKey === 'custom' && (
                        <span className="ml-1 text-amber-600">(Manuel)</span>
                      )}
                      {!distributionTariffKey && (
                        <span className="ml-1 text-gray-400">(Faturadan)</span>
                      )}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Analiz Butonu */}
            <button
              onClick={handleAnalyze}
              disabled={!file || loading}
              className="btn-primary w-full flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Analiz Ediliyor...
                </>
              ) : (
                <>
                  <Zap className="w-5 h-5" />
                  Faturayı Analiz Et
                </>
              )}
            </button>

            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-700">{String(error)}</p>
              </div>
            )}
          </div>

          {/* Sağ Panel - Sonuçlar */}
          <div className="lg:col-span-2 space-y-6">
            {/* Hesaplama Hatası Durumu */}
            {result && result.calculation_error && (
              <div className="card bg-gradient-to-br from-red-50 to-red-100 border-red-300">
                <div className="flex items-start gap-3">
                  <AlertCircle className="w-6 h-6 text-red-500 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="text-lg font-semibold text-red-900 mb-2">Hesaplama Yapılamadı</h3>
                    <p className="text-sm text-red-700 mb-3">{result.calculation_error}</p>
                    {result.extraction && (
                      <div className="mt-4 p-3 bg-white/50 rounded-lg">
                        <p className="text-sm text-gray-600 mb-2">Fatura bilgileri okundu:</p>
                        <div className="grid grid-cols-2 gap-2 text-sm">
                          <div>
                            <span className="text-gray-500">Tedarikçi:</span>{' '}
                            <span className="font-medium">{result.extraction.vendor || '-'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500">Dönem:</span>{' '}
                            <span className="font-medium">{result.extraction.invoice_period || '-'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500">Tüketim:</span>{' '}
                            <span className="font-medium">{result.extraction.consumption_kwh?.value?.toLocaleString('tr-TR')} kWh</span>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
            
            {result && liveCalculation ? (
              <>
                {/* Özet Kartları */}
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                  <div className="card bg-gradient-to-br from-blue-50 to-blue-100 border-blue-200">
                    <p className="text-sm text-blue-600 font-medium">Mevcut Fatura</p>
                    <p className="text-2xl font-bold text-blue-900 mt-1">
                      {formatCurrency(liveCalculation.current_total_with_vat_tl)}
                    </p>
                  </div>
                  
                  <div className="card bg-gradient-to-br from-primary-50 to-primary-100 border-primary-200">
                    <p className="text-sm text-primary-600 font-medium">Teklif Tutarı</p>
                    <p className="text-2xl font-bold text-primary-900 mt-1">
                      {formatCurrency(liveCalculation.offer_total_with_vat_tl)}
                    </p>
                  </div>
                  
                  <div className={`card ${
                    liveCalculation.savings_ratio > 0
                      ? 'bg-gradient-to-br from-green-50 to-green-100 border-green-200'
                      : 'bg-gradient-to-br from-red-50 to-red-100 border-red-200'
                  }`}>
                    <p className={`text-sm font-medium ${
                      liveCalculation.savings_ratio > 0 ? 'text-green-600' : 'text-red-600'
                    }`}>
                      Müşteri Tasarrufu
                    </p>
                    <div className="flex items-baseline gap-2 mt-1">
                      <p className={`text-2xl font-bold ${
                        liveCalculation.savings_ratio > 0 ? 'text-green-900' : 'text-red-900'
                      }`}>
                        {formatPercent(Math.abs(liveCalculation.savings_ratio))}
                      </p>
                      <TrendingDown className={`w-5 h-5 ${
                        liveCalculation.savings_ratio > 0 ? 'text-green-600' : 'text-red-600 rotate-180'
                      }`} />
                    </div>
                    <p className={`text-sm mt-1 ${
                      liveCalculation.savings_ratio > 0 ? 'text-green-700' : 'text-red-700'
                    }`}>
                      {formatCurrency(Math.abs(liveCalculation.difference_incl_vat_tl))}
                    </p>
                  </div>
                  
                  {/* Tedarikçi Karı */}
                  <div className="card bg-gradient-to-br from-purple-50 to-purple-100 border-purple-200">
                    <p className="text-sm text-purple-600 font-medium">Tedarikçi Karı</p>
                    <p className="text-2xl font-bold text-purple-900 mt-1">
                      {formatCurrency(liveCalculation.supplier_profit_tl)}
                    </p>
                    <p className="text-xs text-purple-600 mt-1">
                      %{liveCalculation.supplier_profit_margin.toFixed(1)} marj
                    </p>
                  </div>
                </div>

                {/* Fatura Detayları */}
                <div className="card">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                    <FileText className="w-5 h-5 text-primary-600" />
                    Fatura Bilgileri
                  </h3>
                  
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <p className="text-sm text-gray-500">Tedarikçi</p>
                      <p className="font-medium text-gray-900 capitalize">
                        {result.extraction.vendor || 'Bilinmiyor'}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-gray-500">Dönem</p>
                      <p className="font-medium text-gray-900">
                        {result.extraction.invoice_period || '-'}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-gray-500">Tüketim</p>
                      <p className="font-medium text-gray-900">
                        {result.extraction.consumption_kwh?.value?.toLocaleString('tr-TR')} kWh
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-gray-500">Birim Fiyat</p>
                      <p className="font-medium text-gray-900">
                        {result.extraction.current_active_unit_price_tl_per_kwh?.value?.toFixed(4)} TL/kWh
                      </p>
                    </div>
                  </div>
                  
                  {/* Dağıtım Bedeli Bilgisi */}
                  <div className="mt-4 pt-4 border-t border-gray-100">
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                      <div>
                        <p className="text-sm text-gray-500">Dağıtım Birim Fiyatı</p>
                        <p className="font-medium text-gray-900">
                          {liveCalculation?.distribution_unit_price?.toFixed(6)} TL/kWh
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Kaynak</p>
                        <p className="font-medium text-gray-900">
                          {result.calculation?.meta_distribution_source?.startsWith('epdk_tariff') ? (
                            <span className="text-primary-600">EPDK Tarifesi</span>
                          ) : result.calculation?.meta_distribution_source === 'manual_override' ? (
                            <span className="text-amber-600">Manuel</span>
                          ) : result.calculation?.meta_distribution_source === 'extracted_from_invoice' ? (
                            <span className="text-gray-600">Faturadan</span>
                          ) : (
                            <span className="text-red-600">Bulunamadı</span>
                          )}
                        </p>
                      </div>
                      {result.calculation?.meta_distribution_tariff_key && (
                        <div>
                          <p className="text-sm text-gray-500">Tarife</p>
                          <p className="font-medium text-gray-900 text-xs">
                            {result.calculation.meta_distribution_tariff_key}
                          </p>
                        </div>
                      )}
                    </div>
                    {result.calculation?.meta_distribution_mismatch_warning && (
                      <div className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
                        ⚠️ {result.calculation.meta_distribution_mismatch_warning}
                      </div>
                    )}
                  </div>

                  {/* Validasyon Durumu */}
                  <div className="mt-4 pt-4 border-t border-gray-100">
                    <div className="flex items-center gap-2">
                      {result.validation.is_ready_for_pricing ? (
                        <>
                          <CheckCircle className="w-5 h-5 text-green-500" />
                          <span className="text-sm text-green-700 font-medium">
                            Fatura analizi başarılı
                          </span>
                        </>
                      ) : (
                        <>
                          <AlertCircle className="w-5 h-5 text-amber-500" />
                          <span className="text-sm text-amber-700 font-medium">
                            Eksik alanlar: {result.validation.missing_fields.join(', ')}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  
                  {/* Hesap Detayı - Debug Panel */}
                  {result.debug_meta && (
                    <details className="mt-4 pt-4 border-t border-gray-100">
                      <summary className="cursor-pointer text-sm font-medium text-gray-600 hover:text-gray-900 flex items-center gap-2">
                        <span>🔍 Hesap Detayı</span>
                        <span className="text-xs text-gray-400">(trace: {result.debug_meta.trace_id || result.meta?.trace_id})</span>
                        {/* Quality Score Badge */}
                        {result.quality_score && (
                          <span className={`ml-2 px-2 py-0.5 rounded text-xs font-medium ${
                            result.quality_score.grade === 'OK' ? 'bg-green-100 text-green-700' :
                            result.quality_score.grade === 'CHECK' ? 'bg-yellow-100 text-yellow-700' :
                            'bg-red-100 text-red-700'
                          }`}>
                            {result.quality_score.score} {result.quality_score.grade}
                          </span>
                        )}
                      </summary>
                      <div className="mt-3 p-3 bg-gray-50 rounded-lg text-xs space-y-2">
                        {/* Quality Score Details */}
                        {result.quality_score && result.quality_score.flags.length > 0 && (
                          <div className="pb-2 border-b border-gray-200">
                            <span className="text-gray-600 font-medium">Kalite Bayrakları:</span>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {result.quality_score.flag_details.map((flag, i) => (
                                <span key={i} className={`inline-flex items-center px-2 py-0.5 rounded text-xs ${
                                  flag.severity === 'S1' ? 'bg-red-100 text-red-700' :
                                  flag.severity === 'S2' ? 'bg-orange-100 text-orange-700' :
                                  flag.severity === 'S3' ? 'bg-yellow-100 text-yellow-700' :
                                  'bg-blue-100 text-blue-700'
                                }`} title={flag.message}>
                                  {flag.code} (-{flag.deduction})
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        
                        {/* Fiyatlama Kaynağı */}
                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <span className="text-gray-500">Dönem:</span>{' '}
                            <span className="font-mono">{result.debug_meta.pricing_period || result.calculation?.meta_pricing_period || '-'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500">Kaynak:</span>{' '}
                            <span className={`font-medium ${
                              result.debug_meta.pricing_source === 'reference' ? 'text-primary-600' :
                              result.debug_meta.pricing_source === 'override' ? 'text-amber-600' : 'text-gray-600'
                            }`}>
                              {result.debug_meta.pricing_source || result.calculation?.meta_pricing_source || '-'}
                            </span>
                          </div>
                          <div>
                            <span className="text-gray-500">PTF:</span>{' '}
                            <span className="font-mono">{result.debug_meta.ptf_tl_per_mwh || result.calculation?.meta_ptf_tl_per_mwh || 0} TL/MWh</span>
                          </div>
                          <div>
                            <span className="text-gray-500">YEKDEM:</span>{' '}
                            <span className="font-mono">{result.debug_meta.yekdem_tl_per_mwh || result.calculation?.meta_yekdem_tl_per_mwh || 0} TL/MWh</span>
                          </div>
                        </div>
                        
                        {/* Dağıtım Tarifesi */}
                        <div className="grid grid-cols-2 gap-2 pt-2 border-t border-gray-200">
                          <div>
                            <span className="text-gray-500">EPDK Tarife:</span>{' '}
                            <span className="font-mono text-xs">{result.debug_meta.epdk_tariff_key || result.calculation?.meta_distribution_tariff_key || '-'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500">Dağıtım B/F:</span>{' '}
                            <span className="font-mono">{(result.debug_meta.distribution_unit_price_tl_per_kwh || result.calculation?.offer_distribution_unit_tl_per_kwh || 0).toFixed(6)} TL/kWh</span>
                          </div>
                        </div>
                        
                        {/* Uyarılar */}
                        {result.debug_meta.warnings && result.debug_meta.warnings.length > 0 && (
                          <div className="pt-2 border-t border-gray-200">
                            <span className="text-amber-600 font-medium">⚠️ Uyarılar:</span>
                            <ul className="mt-1 list-disc list-inside text-amber-700">
                              {result.debug_meta.warnings.map((w, i) => <li key={i}>{w}</li>)}
                            </ul>
                          </div>
                        )}
                        
                        {/* Hatalar */}
                        {result.debug_meta.errors && result.debug_meta.errors.length > 0 && (
                          <div className="pt-2 border-t border-gray-200">
                            <span className="text-red-600 font-medium">❌ Hatalar:</span>
                            <ul className="mt-1 list-disc list-inside text-red-700">
                              {result.debug_meta.errors.map((e, i) => <li key={i}>{e}</li>)}
                            </ul>
                          </div>
                        )}
                        
                        {/* Model Bilgisi */}
                        {result.debug_meta.llm_model_used && (
                          <div className="pt-2 border-t border-gray-200 text-gray-500">
                            Model: {result.debug_meta.llm_model_used}
                            {result.debug_meta.extraction_cache_hit && <span className="ml-2 text-green-600">(cache hit)</span>}
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </div>

                {/* Karşılaştırma Tablosu */}
                <div className="card">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">
                    Detaylı Karşılaştırma
                  </h3>
                  
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Kalem</th>
                          <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">Mevcut</th>
                          <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">Teklif</th>
                          <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">Fark</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        <tr>
                          <td className="py-3 px-4 text-sm text-gray-700">Enerji Bedeli</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_energy_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.offer_energy_tl)}
                          </td>
                          <td className={`py-3 px-4 text-sm text-right font-medium ${
                            liveCalculation.current_energy_tl > liveCalculation.offer_energy_tl
                              ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {formatCurrency(liveCalculation.current_energy_tl - liveCalculation.offer_energy_tl)}
                          </td>
                        </tr>
                        <tr>
                          <td className="py-3 px-4 text-sm text-gray-700">Dağıtım Bedeli</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_distribution_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.offer_distribution_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-500">-</td>
                        </tr>
                        <tr>
                          <td className="py-3 px-4 text-sm text-gray-700">BTV</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_btv_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.offer_btv_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-500">-</td>
                        </tr>
                        <tr>
                          <td className="py-3 px-4 text-sm text-gray-700">KDV Matrahı</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_vat_matrah_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.offer_vat_matrah_tl)}
                          </td>
                          <td className={`py-3 px-4 text-sm text-right font-medium ${
                            liveCalculation.difference_excl_vat_tl > 0
                              ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {formatCurrency(liveCalculation.difference_excl_vat_tl)}
                          </td>
                        </tr>
                        <tr>
                          <td className="py-3 px-4 text-sm text-gray-700">KDV (%20)</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_vat_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.offer_vat_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-gray-500">-</td>
                        </tr>
                        <tr className="bg-gray-50 font-semibold">
                          <td className="py-3 px-4 text-sm text-gray-900">TOPLAM</td>
                          <td className="py-3 px-4 text-sm text-right text-gray-900">
                            {formatCurrency(liveCalculation.current_total_with_vat_tl)}
                          </td>
                          <td className="py-3 px-4 text-sm text-right text-primary-600">
                            {formatCurrency(liveCalculation.offer_total_with_vat_tl)}
                          </td>
                          <td className={`py-3 px-4 text-sm text-right ${
                            liveCalculation.difference_incl_vat_tl > 0
                              ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {formatCurrency(liveCalculation.difference_incl_vat_tl)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Aksiyon Butonları */}
                <div className="flex gap-4">
                  <button
                    onClick={handleDownloadPdf}
                    disabled={pdfLoading}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    {pdfLoading ? (
                      <>
                        <Loader2 className="w-5 h-5 animate-spin" />
                        PDF Hazırlanıyor...
                      </>
                    ) : (
                      <>
                        <Download className="w-5 h-5" />
                        Teklif PDF İndir
                      </>
                    )}
                  </button>
                  
                  <button
                    onClick={handleReset}
                    className="btn-secondary flex-1 flex items-center justify-center gap-2"
                  >
                    <RefreshCw className="w-5 h-5" />
                    Yeni Fatura
                  </button>
                </div>
              </>
            ) : (
              /* Boş Durum */
              <div className="card h-full min-h-[400px] flex items-center justify-center">
                <div className="text-center">
                  <div className="w-20 h-20 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <FileText className="w-10 h-10 text-gray-400" />
                  </div>
                  <h3 className="text-lg font-medium text-gray-900 mb-2">
                    Fatura Yükleyin
                  </h3>
                  <p className="text-gray-500 max-w-sm">
                    Elektrik faturanızı yükleyin, yapay zeka ile analiz edelim ve size en uygun teklifi hesaplayalım.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 bg-white mt-12">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <p className="text-sm text-gray-500 text-center">
            © 2026 Gelka Enerji. Tüm hakları saklıdır.
          </p>
        </div>
      </footer>
    </div>
  );
}

export default App;
