import { useState, useCallback, useMemo, useEffect } from 'react';
import { Upload, FileText, Zap, TrendingDown, AlertCircle, CheckCircle, Loader2, RefreshCw, Download, Settings } from 'lucide-react';
import { fullProcess, generateOfferPdf, FullProcessResponse, syncEpiasPrices } from './api';
import AdminPanel from './AdminPanel';

// EPDK Dağıtım Tarifeleri (Şubat 2026)
const DISTRIBUTION_TARIFFS = [
  // EPDK Dağıtım Tarifeleri — Şubat 2026 (24 satır, tablodaki sırayla)
  { key: 'isk_sanayi', label: 'İSK Sanayi', price: 0.00000, group: 'sanayi' },
  { key: 'dsk_sanayi_ct_og', label: 'DSK Sanayi ÇT OG', price: 0.81060, group: 'sanayi' },
  { key: 'dsk_sanayi_tt_og', label: 'DSK Sanayi TT OG', price: 0.89537, group: 'sanayi' },
  { key: 'dsk_ticarethane_ct_og', label: 'DSK Ticarethane ÇT OG', price: 1.26329, group: 'ticarethane' },
  { key: 'dsk_mesken_ct_og', label: 'DSK Mesken ÇT OG', price: 1.25129, group: 'mesken' },
  { key: 'dsk_aydinlatma_ct_og', label: 'DSK Aydınlatma ÇT OG', price: 1.21249, group: 'aydinlatma' },
  { key: 'dsk_tarimsal_ct_og', label: 'DSK Tarımsal ÇT OG', price: 1.04042, group: 'tarimsal' },
  { key: 'dsk_sanayi_ct_og_2', label: 'DSK Sanayi ÇT OG', price: 0.81060, group: 'sanayi' },
  { key: 'dsk_sanayi_tt_og_2', label: 'DSK Sanayi TT OG', price: 0.89537, group: 'sanayi' },
  { key: 'dsk_ticarethane_tt_og', label: 'DSK Ticarethane TT OG', price: 1.57581, group: 'ticarethane' },
  { key: 'dsk_mesken_tt_og', label: 'DSK Mesken TT OG', price: 1.54502, group: 'mesken' },
  { key: 'dsk_aydinlatma_tt_og', label: 'DSK Aydınlatma TT OG', price: 1.51248, group: 'aydinlatma' },
  { key: 'dsk_tarimsal_tt_og', label: 'DSK Tarımsal TT OG', price: 1.29543, group: 'tarimsal' },
  { key: 'dsk_sanayi_ct_og_3', label: 'DSK Sanayi ÇT OG', price: 0.81060, group: 'sanayi' },
  { key: 'dsk_sanayi_tt_og_3', label: 'DSK Sanayi TT OG', price: 0.89537, group: 'sanayi' },
  { key: 'dsk_sanayi_tt_ag', label: 'DSK Sanayi TT AG', price: 1.38532, group: 'sanayi' },
  { key: 'dsk_ticarethane_tt_ag', label: 'DSK Ticarethane TT AG', price: 1.87741, group: 'ticarethane' },
  { key: 'dsk_ticarethane_tt_ag_2', label: 'DSK Ticarethane TT AG', price: 1.87741, group: 'ticarethane' },
  { key: 'dsk_ticarethane_tt_ag_3', label: 'DSK Ticarethane TT AG', price: 1.87741, group: 'ticarethane' },
  { key: 'dsk_mesken_tt_ag', label: 'DSK Mesken TT AG', price: 1.83617, group: 'mesken' },
  { key: 'dsk_mesken_sehit_gazi', label: 'DSK Mesken Şehit Gazi', price: 1.03557, group: 'mesken' },
  { key: 'dsk_mesken_tt_ag_2', label: 'DSK Mesken TT AG', price: 1.83617, group: 'mesken' },
  { key: 'dsk_tarimsal_tt_ag', label: 'DSK Tarımsal TT AG', price: 1.54263, group: 'tarimsal' },
  { key: 'dsk_aydinlatma_tt_ag', label: 'DSK Aydınlatma TT AG', price: 1.79815, group: 'aydinlatma' },
  // Manuel giriş
  { key: 'custom', label: 'Manuel Giriş', price: 0, group: 'custom' },
];

// Son 24 ay için dönem seçenekleri oluştur
const generatePeriodOptions = () => {
  const options: { value: string; label: string }[] = [];
  const now = new Date();
  
  // Türkçe ay isimleri
  const monthNames = [
    'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
    'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
  ];
  
  for (let i = 0; i < 24; i++) {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const year = date.getFullYear();
    const month = date.getMonth();
    const value = `${year}-${String(month + 1).padStart(2, '0')}`;
    const label = `${monthNames[month]} ${year}`;
    options.push({ value, label });
  }
  
  return options;
};

const PERIOD_OPTIONS = generatePeriodOptions();

function App() {
  // Türkçe sayı formatını parse et: 58.761,15 -> 58761.15
  const parseNumber = (value: string): number => {
    // Önce binlik ayırıcı noktaları kaldır, sonra virgülü noktaya çevir
    const normalized = value.replace(/\./g, '').replace(',', '.');
    return parseFloat(normalized) || 0;
  };
  
  // Sayıyı Türkçe formata çevir: 58761.15 -> 58.761,15
  const formatNumber = (value: number): string => {
    if (!value) return '';
    return value.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  
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
  
  // PTF/YEKDEM fiyat çekme durumu
  const [priceLoading, setPriceLoading] = useState(false);
  const [priceError, setPriceError] = useState<string | null>(null);
  
  // Dağıtım birim fiyatı (manuel override)
  const [distributionTariffKey, setDistributionTariffKey] = useState<string>('');
  const [customDistributionPrice, setCustomDistributionPrice] = useState<number>(0);
  
  // BTV oranı: Sanayi %1, Ticarethane/Kamu/Özel %5
  const [btvRate, setBtvRate] = useState<number>(0.01);
  
  // Müşteri bilgileri (PDF için)
  const [customerInfo, setCustomerInfo] = useState({
    company_name: '',      // Firma adı
    contact_person: '',    // Yetkili kişi
    offer_date: new Date().toISOString().split('T')[0],  // Teklif tarihi (YYYY-MM-DD)
    offer_validity_days: 15,  // Teklif geçerlilik süresi (gün)
  });
  
  // KDV oranı: Normal %20, Tarımsal Sulama %10
  const [vatRate, setVatRate] = useState<number>(0.20);
  
  // Manuel fatura değerleri override
  const [manualMode, setManualMode] = useState(false);
  const [consumptionInput, setConsumptionInput] = useState('');
  const [manualValues, setManualValues] = useState({
    consumption_kwh: 0,
    current_unit_price: 0,
    current_energy_tl: 0,
    current_distribution_tl: 0,
    current_btv_tl: 0,
    current_vat_matrah_tl: 0,
    current_vat_tl: 0,
    current_total_with_vat_tl: 0,
    vendor: '',
    invoice_period: '',
    tariff_group: '',
  });
  
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
    // Manuel mod aktifse, manuel değerlerle hesapla
    if (manualMode && manualValues.consumption_kwh > 0) {
      const kwh = manualValues.consumption_kwh;
      const distUnitPrice = getDistributionUnitPrice() || (manualValues.current_distribution_tl / kwh);
      
      const ptfKwh = ptfPrice / 1000;
      const yekdemKwh = yekdemPrice / 1000;
      
      // Mevcut fatura değerleri: Manuel girişten enerji ve dağıtım
      const current_energy_tl = manualValues.current_energy_tl;
      const current_distribution_tl = manualValues.current_distribution_tl;
      // BTV ve KDV: Seçilen oranlara göre HESAPLA
      const current_btv_tl = current_energy_tl * btvRate;
      const current_vat_matrah_tl = current_energy_tl + current_distribution_tl + current_btv_tl;
      const current_vat_tl = current_vat_matrah_tl * vatRate;
      const current_total_with_vat_tl = current_vat_matrah_tl + current_vat_tl;
      
      // YEKDEM dahil et (manuel modda her zaman dahil)
      const includeYekdem = yekdemPrice > 0;
      const offerBasePrice = includeYekdem ? (ptfKwh + yekdemKwh) : ptfKwh;
      
      // Teklif hesaplama
      const offer_energy_base = kwh * offerBasePrice;
      const offer_energy_tl = offer_energy_base * multiplier;
      const offer_distribution_tl = kwh * distUnitPrice;
      // BTV oranı: Sanayi %1, Ticarethane/Kamu/Özel %5
      const offer_btv_tl = offer_energy_tl * btvRate;
      const offer_vat_matrah_tl = offer_energy_tl + offer_distribution_tl + offer_btv_tl;
      const offer_vat_tl = offer_vat_matrah_tl * vatRate;
      const offer_total_with_vat_tl = offer_vat_matrah_tl + offer_vat_tl;
      
      // Fark ve tasarruf
      const difference_excl_vat_tl = current_vat_matrah_tl - offer_vat_matrah_tl;
      const difference_incl_vat_tl = current_total_with_vat_tl - offer_total_with_vat_tl;
      const savings_ratio = current_total_with_vat_tl > 0 ? difference_incl_vat_tl / current_total_with_vat_tl : 0;
      
      // Tedarikçi karı
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
        include_yekdem: includeYekdem,
        distribution_unit_price: distUnitPrice,
      };
    }
    
    if (!result?.extraction) return null;
    
    // Backend'den gelen calculation'ı temel al
    const backendCalc = result.calculation;
    
    // Eğer parametreler değiştiyse frontend'de yeniden hesapla
    const kwh = result.extraction.consumption_kwh?.value || 0;
    const distUnitPrice = getDistributionUnitPrice();
    
    const ptfKwh = ptfPrice / 1000;
    const yekdemKwh = yekdemPrice / 1000;
    
    // Mevcut fatura değerleri: Backend'den gelen enerji ve dağıtım
    const current_energy_tl = backendCalc?.current_energy_tl || 0;
    const backendDistUnitPrice = result.extraction.distribution_unit_price_tl_per_kwh?.value || 0;
    const current_distribution_tl = backendCalc?.current_distribution_tl || (kwh * backendDistUnitPrice);
    
    // BTV ve KDV: Seçilen oranlara göre HESAPLA (karşılaştırma için)
    const current_btv_tl = current_energy_tl * btvRate;
    const current_vat_matrah_tl = current_energy_tl + current_distribution_tl + current_btv_tl;
    const current_vat_tl = current_vat_matrah_tl * vatRate;
    const current_total_with_vat_tl = current_vat_matrah_tl + current_vat_tl;
    
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
    // BTV oranı: Sanayi %1, Ticarethane/Kamu/Özel %5
    // BTV oranı: Sanayi %1, Ticarethane/Kamu/Özel %5
    const offer_btv_tl = offer_energy_tl * btvRate;
    const offer_vat_matrah_tl = offer_energy_tl + offer_distribution_tl + offer_btv_tl;
    const offer_vat_tl = offer_vat_matrah_tl * vatRate;
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
  }, [result?.extraction, result?.calculation, ptfPrice, yekdemPrice, multiplier, getDistributionUnitPrice, manualMode, manualValues, btvRate, vatRate]);

  // Dağıtım bedeli otomatik hesaplama: dağıtım birim fiyatı × kWh
  useEffect(() => {
    if (!manualMode) return;
    const distPrice = getDistributionUnitPrice();
    const kwh = manualValues.consumption_kwh;
    if (distPrice > 0 && kwh > 0) {
      const autoVal = distPrice * kwh;
      if (Math.abs(manualValues.current_distribution_tl - autoVal) > 0.01) {
        setManualValues(prev => ({...prev, current_distribution_tl: autoVal}));
      }
    }
  }, [manualMode, manualValues.consumption_kwh, getDistributionUnitPrice]);

  // Dönem değiştiğinde PTF/YEKDEM fiyatlarını otomatik çek
  useEffect(() => {
    // Sadece manuel modda ve dönem seçilmişse çalış
    if (!manualMode || !manualValues.invoice_period) return;
    
    // Dönem zaten YYYY-MM formatında (dropdown'dan geliyor)
    const period = manualValues.invoice_period;
    
    const fetchPrices = async () => {
      setPriceLoading(true);
      setPriceError(null);
      
      try {
        // Mock veri kullan (test için) - production'da use_mock=false yapılacak
        const response = await syncEpiasPrices(period, false, true);
        
        if (response.status === 'ok' && response.ptf_tl_per_mwh) {
          setPtfPrice(response.ptf_tl_per_mwh);
          if (response.yekdem_tl_per_mwh !== undefined) {
            setYekdemPrice(response.yekdem_tl_per_mwh);
          }
        }
      } catch (err: any) {
        const errorMsg = err.response?.data?.detail || err.message || 'Fiyat çekilemedi';
        setPriceError(typeof errorMsg === 'string' ? errorMsg : JSON.stringify(errorMsg));
      } finally {
        setPriceLoading(false);
      }
    };
    
    fetchPrices();
  }, [manualMode, manualValues.invoice_period]);

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
    setManualMode(false);
    setManualValues({
      consumption_kwh: 0,
      current_unit_price: 0,
      current_energy_tl: 0,
      current_distribution_tl: 0,
      current_btv_tl: 0,
      current_vat_matrah_tl: 0,
      current_vat_tl: 0,
      current_total_with_vat_tl: 0,
      vendor: '',
      invoice_period: '',
      tariff_group: '',
    });
  };

  const handleDownloadPdf = async () => {
    if (!liveCalculation) return;
    
    // Seçili tarife grubunu belirle
    const selectedTariffLabel = distributionTariffKey 
      ? DISTRIBUTION_TARIFFS.find(t => t.key === distributionTariffKey)?.label || manualValues.tariff_group
      : manualValues.tariff_group;
    
    // Manuel modda veya OCR modda extraction oluştur
    const extraction = manualMode ? {
      vendor: manualValues.vendor || 'Manuel Giriş',
      invoice_period: manualValues.invoice_period || '-',
      consumption_kwh: { value: manualValues.consumption_kwh, confidence: 1.0 },
      current_active_unit_price_tl_per_kwh: { value: manualValues.current_unit_price, confidence: 1.0 },
      distribution_unit_price_tl_per_kwh: { value: liveCalculation.distribution_unit_price, confidence: 1.0 },
      meta: { tariff_group_guess: selectedTariffLabel || 'Sanayi' },
    } : {
      ...result?.extraction,
      meta: { 
        ...result?.extraction?.meta,
        tariff_group_guess: selectedTariffLabel || result?.extraction?.meta?.tariff_group_guess || 'Sanayi' 
      },
    };
    
    if (!extraction) return;
    
    setPdfLoading(true);
    try {
      // Seçili tarife grubunun tam label'ını al
      const tariffLabel = distributionTariffKey 
        ? DISTRIBUTION_TARIFFS.find(t => t.key === distributionTariffKey)?.label 
        : manualValues.tariff_group || 'Sanayi';
      
      // DEBUG: PDF'e gönderilen değerleri logla
      console.log('PDF Generation Debug:', {
        customerName: customerInfo.company_name,
        contactPerson: customerInfo.contact_person,
        tariffLabel,
        distributionTariffKey,
        vendor: extraction.vendor,
        offer_energy_tl: liveCalculation.offer_energy_tl,
        offer_total_with_vat_tl: liveCalculation.offer_total_with_vat_tl,
      });
      
      const pdfBlob = await generateOfferPdf(
        extraction,
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
          meta_vat_rate: vatRate,
        },
        {
          weighted_ptf_tl_per_mwh: ptfPrice,
          yekdem_tl_per_mwh: liveCalculation.include_yekdem ? yekdemPrice : 0,
          agreement_multiplier: multiplier,
        },
        customerInfo.company_name || undefined,  // customer_name
        customerInfo.contact_person || undefined,  // contact_person
        customerInfo.offer_date || undefined,  // offer_date
        customerInfo.offer_validity_days || 15,  // offer_validity_days
        tariffLabel || 'Sanayi'  // tariff_group
      );
      
      // Download the PDF
      const url = window.URL.createObjectURL(pdfBlob);
      const link = document.createElement('a');
      link.href = url;
      const period = manualMode ? manualValues.invoice_period : result?.extraction?.invoice_period;
      link.download = `teklif_${period || 'fatura'}.pdf`;
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
    <div className="h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 flex-shrink-0">
        <div className="max-w-7xl mx-auto px-4 py-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center">
                <Zap className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-bold text-gray-900">Gelka Enerji</h1>
                <p className="text-xs text-gray-500">Fatura Analiz Sistemi</p>
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

      <main className="flex-1 max-w-7xl mx-auto px-4 py-3 w-full overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 h-full">
          {/* Sol Panel - Yükleme ve Parametreler */}
          <div className="lg:col-span-1 space-y-2 overflow-hidden">
            {/* Dosya Yükleme */}
            <div className="card p-3">
              <h2 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
                <Upload className="w-4 h-4 text-primary-600" />
                Fatura Yükle
              </h2>
              
              <div
                className={`border-2 border-dashed rounded-lg p-3 text-center transition-colors ${
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
                  <div className="space-y-1">
                    <FileText className="w-8 h-8 text-primary-600 mx-auto" />
                    <p className="font-medium text-gray-900 text-sm truncate">{file.name}</p>
                    <p className="text-xs text-gray-500">
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
            <div className="card p-3">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-semibold text-gray-900">Teklif Parametreleri</h2>
                <button
                  type="button"
                  onClick={() => setUseReferencePrices(!useReferencePrices)}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    useReferencePrices ? 'bg-primary-600' : 'bg-gray-300'
                  }`}
                  title={useReferencePrices ? 'Otomatik (DB)' : 'Manuel'}
                >
                  <span
                    className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                      useReferencePrices ? 'translate-x-5' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
              
              <div className="space-y-2">
                {/* Kaynak Badge - sadece sonuç varsa göster */}
                {result?.calculation?.meta_pricing_source && (
                  <div className="flex items-center gap-1 text-xs">
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded font-medium ${
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
                      <span className="text-gray-500">{result.calculation.meta_pricing_period}</span>
                    )}
                  </div>
                )}
                
                {/* PTF ve YEKDEM yan yana */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs font-medium text-gray-700 mb-1 block">
                      PTF (TL/MWh)
                      {priceLoading && <Loader2 className="w-3 h-3 inline ml-1 animate-spin text-primary-500" />}
                      {useReferencePrices && !manualMode && result?.calculation?.meta_ptf_tl_per_mwh && (
                        <span className="text-primary-600 ml-1">({result.calculation.meta_ptf_tl_per_mwh})</span>
                      )}
                    </label>
                    <input
                      type="number"
                      className={`w-full px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none ${useReferencePrices && !manualMode ? 'bg-gray-50' : ''} ${priceLoading ? 'animate-pulse' : ''}`}
                      value={ptfPrice}
                      onChange={(e) => setPtfPrice(parseFloat(e.target.value) || 0)}
                      step="0.1"
                      disabled={useReferencePrices && !manualMode}
                    />
                  </div>
                  <div>
                    {(() => {
                      const yekdemDisabled = !!(liveCalculation && !liveCalculation.include_yekdem && !manualMode) || (useReferencePrices && !manualMode);
                      const yekdemVal = liveCalculation && !liveCalculation.include_yekdem && !manualMode ? 0 : yekdemPrice;
                      const yekdemPresets = [
                        { label: 'Oca 26 — 274,89', value: 274.89 },
                        { label: 'Şub 26 — 201,41', value: 201.41 },
                        { label: 'Mar 26 — 460,88', value: 460.88 },
                        { label: 'Nis 26 — 441,29', value: 441.29 },
                        { label: 'May 26 — 563,78', value: 563.78 },
                        { label: 'Haz 26 — 617,89', value: 617.89 },
                        { label: 'Tem 26 — 292,21', value: 292.21 },
                        { label: 'Ağu 26 — 302,65', value: 302.65 },
                        { label: 'Eyl 26 — 395,98', value: 395.98 },
                        { label: 'Eki 26 — 416,69', value: 416.69 },
                        { label: 'Kas 26 — 374,19', value: 374.19 },
                        { label: 'Ara 26 — 281,23', value: 281.23 },
                      ];
                      return (
                        <>
                          <label className="text-xs font-medium text-gray-700 mb-1 block">
                            YEKDEM (TL/MWh)
                            {liveCalculation && !liveCalculation.include_yekdem && !manualMode && (
                              <span className="text-gray-400 ml-1">(yok)</span>
                            )}
                          </label>
                          <div className="relative">
                            <input
                              type="number"
                              className={`w-full px-2 py-1.5 pr-7 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none ${yekdemDisabled ? 'bg-gray-100 text-gray-400' : ''}`}
                              value={yekdemVal}
                              onChange={(e) => setYekdemPrice(parseFloat(e.target.value) || 0)}
                              step="0.1"
                              disabled={yekdemDisabled}
                            />
                            <select
                              className="absolute inset-0 opacity-0 cursor-pointer"
                              value=""
                              onChange={(e) => {
                                if (e.target.value) {
                                  setYekdemPrice(parseFloat(e.target.value));
                                }
                              }}
                              disabled={yekdemDisabled}
                            >
                              <option value="">Seç...</option>
                              {yekdemPresets.map(p => (
                                <option key={p.value} value={p.value.toString()}>{p.label}</option>
                              ))}
                            </select>
                            {!yekdemDisabled && (
                              <div className="absolute right-1.5 top-1/2 -translate-y-1/2 pointer-events-none text-gray-400">
                                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                              </div>
                            )}
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </div>
                
                {/* Çarpan */}
                <div>
                  <label className="text-xs font-medium text-gray-700 mb-1 block">Çarpan (Kar Marjı)</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      className="w-20 px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none"
                      value={multiplier}
                      onChange={(e) => setMultiplier(parseFloat(e.target.value) || 1)}
                      step="0.01"
                      min="1"
                      max="2"
                    />
                    <div className="flex flex-wrap gap-1">
                      {multiplierOptions.map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => setMultiplier(opt.value)}
                          className={`px-1.5 py-0.5 text-xs rounded transition-colors ${
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
                </div>
                
                {/* Dağıtım Bedeli */}
                <div className="pt-2 border-t border-gray-100">
                  <label className="text-xs font-medium text-gray-700 mb-1 block">Dağıtım Bedeli</label>
                  <select
                    className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none"
                    value={distributionTariffKey}
                    onChange={(e) => {
                      const key = e.target.value;
                      setDistributionTariffKey(key);
                      if (key !== 'custom') {
                        setCustomDistributionPrice(0);
                      }
                      // Tarife grubuna göre BTV ve KDV oranlarını otomatik ayarla
                      const tariff = DISTRIBUTION_TARIFFS.find(t => t.key === key);
                      if (tariff) {
                        setManualValues(prev => ({...prev, tariff_group: tariff.label}));
                        
                        // BTV Oranları (2464 sayılı Kanun Madde 34):
                        // Sanayi (imal/istihsal kapsamı): %1
                        // Diğer tüm gruplar: %5
                        if (tariff.group === 'sanayi') {
                          setBtvRate(0.01);
                        } else if (tariff.group !== 'custom') {
                          setBtvRate(0.05);
                        }
                        
                        // KDV Oranları:
                        // Mesken ve Tarımsal: %10 (indirimli - II sayılı liste)
                        // Sanayi, Ticarethane, Aydınlatma: %20
                        if (tariff.group === 'tarimsal' || tariff.group === 'mesken') {
                          setVatRate(0.10);
                        } else if (tariff.group !== 'custom') {
                          setVatRate(0.20);
                        }
                      }
                    }}
                  >
                    <option value="">Faturadan (Otomatik)</option>
                    {DISTRIBUTION_TARIFFS.map((tariff) => (
                      <option key={tariff.key} value={tariff.key}>
                        {tariff.label} {tariff.key !== 'custom' ? `— ${(tariff.price * 1000).toFixed(2)} kr/kWh` : ''}
                      </option>
                    ))}
                  </select>
                  
                  {distributionTariffKey === 'custom' && (
                    <input
                      type="number"
                      className="w-full mt-1 px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none"
                      placeholder="1.836166"
                      value={customDistributionPrice || ''}
                      onChange={(e) => setCustomDistributionPrice(parseFloat(e.target.value) || 0)}
                      step="0.000001"
                      min="0"
                    />
                  )}
                  
                  {liveCalculation && (
                    <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                      <p>Birim Fiyat: <span className="font-medium text-gray-700">{(liveCalculation.distribution_unit_price * 1000).toFixed(2)} kr/kWh</span></p>
                      <p>Dağıtım Bedeli: <span className="font-medium text-gray-900">{formatNumber(liveCalculation.offer_distribution_tl)} TL</span></p>
                    </div>
                  )}
                </div>
                
                {/* BTV Oranı */}
                <div className="pt-2 border-t border-gray-100">
                  <label className="text-xs font-medium text-gray-700 mb-1 block">BTV Oranı</label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setBtvRate(0.01)}
                      className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                        btvRate === 0.01
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      %1 (Sanayi)
                    </button>
                    <button
                      type="button"
                      onClick={() => setBtvRate(0.05)}
                      className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                        btvRate === 0.05
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      %5 (Ticari/Kamu)
                    </button>
                  </div>
                </div>
                
                {/* KDV Oranı */}
                <div className="pt-2 border-t border-gray-100">
                  <label className="text-xs font-medium text-gray-700 mb-1 block">KDV Oranı</label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setVatRate(0.20)}
                      className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                        vatRate === 0.20
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      %20 (Normal)
                    </button>
                    <button
                      type="button"
                      onClick={() => setVatRate(0.10)}
                      className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                        vatRate === 0.10
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      %10 (Mesken/Tarımsal)
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Analiz Butonu */}
            <button
              onClick={handleAnalyze}
              disabled={!file || loading}
              className="btn-primary w-full flex items-center justify-center gap-2 py-2"
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
              <div className="bg-red-50 border border-red-200 rounded-lg p-2 flex items-start gap-2">
                <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-red-700">{String(error)}</p>
              </div>
            )}
          </div>

          {/* Sağ Panel - Sonuçlar */}
          <div className="lg:col-span-2 space-y-2 overflow-hidden">
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
            
            {(result && liveCalculation) || (manualMode && liveCalculation) ? (
              <>
                {/* Özet Kartları */}
                <div className="grid grid-cols-4 gap-2">
                  <div className="card p-2 bg-gradient-to-br from-blue-50 to-blue-100 border-blue-200">
                    <p className="text-xs text-blue-600 font-medium">Mevcut Fatura</p>
                    <p className="text-lg font-bold text-blue-900">
                      {formatCurrency(liveCalculation.current_total_with_vat_tl)}
                    </p>
                  </div>
                  
                  <div className="card p-2 bg-gradient-to-br from-primary-50 to-primary-100 border-primary-200">
                    <p className="text-xs text-primary-600 font-medium">Teklif Tutarı</p>
                    <p className="text-lg font-bold text-primary-900">
                      {formatCurrency(liveCalculation.offer_total_with_vat_tl)}
                    </p>
                  </div>
                  
                  <div className={`card p-2 ${
                    liveCalculation.savings_ratio > 0
                      ? 'bg-gradient-to-br from-green-50 to-green-100 border-green-200'
                      : 'bg-gradient-to-br from-red-50 to-red-100 border-red-200'
                  }`}>
                    <p className={`text-xs font-medium ${
                      liveCalculation.savings_ratio > 0 ? 'text-green-600' : 'text-red-600'
                    }`}>
                      Tasarruf
                    </p>
                    <div className="flex items-baseline gap-1">
                      <p className={`text-lg font-bold ${
                        liveCalculation.savings_ratio > 0 ? 'text-green-900' : 'text-red-900'
                      }`}>
                        {formatPercent(Math.abs(liveCalculation.savings_ratio))}
                      </p>
                      <TrendingDown className={`w-4 h-4 ${
                        liveCalculation.savings_ratio > 0 ? 'text-green-600' : 'text-red-600 rotate-180'
                      }`} />
                    </div>
                    <p className={`text-xs ${
                      liveCalculation.savings_ratio > 0 ? 'text-green-700' : 'text-red-700'
                    }`}>
                      {formatCurrency(Math.abs(liveCalculation.difference_incl_vat_tl))}
                    </p>
                  </div>
                  
                  {/* Tedarikçi Karı */}
                  <div className="card p-2 bg-gradient-to-br from-purple-50 to-purple-100 border-purple-200">
                    <p className="text-xs text-purple-600 font-medium">Tedarikçi Karı</p>
                    <p className="text-lg font-bold text-purple-900">
                      {formatCurrency(liveCalculation.supplier_profit_tl)}
                    </p>
                    <p className="text-xs text-purple-600">
                      %{liveCalculation.supplier_profit_margin.toFixed(1)} marj
                    </p>
                  </div>
                </div>

                {/* Müşteri Bilgileri (PDF için) */}
                <div className="card p-3">
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">📋 Müşteri Bilgileri</h3>
                  <div className="grid grid-cols-4 gap-2">
                    <div className="col-span-2">
                      <label className="text-xs text-gray-500 block mb-1">Firma Adı</label>
                      <input
                        type="text"
                        className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                        value={customerInfo.company_name}
                        onChange={(e) => setCustomerInfo({...customerInfo, company_name: e.target.value})}
                        placeholder="Örn: ABC Sanayi A.Ş."
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">Yetkili Kişi</label>
                      <input
                        type="text"
                        className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                        value={customerInfo.contact_person}
                        onChange={(e) => setCustomerInfo({...customerInfo, contact_person: e.target.value})}
                        placeholder="Örn: Ahmet Yılmaz"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">Teklif Tarihi</label>
                      <input
                        type="date"
                        className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                        value={customerInfo.offer_date}
                        onChange={(e) => setCustomerInfo({...customerInfo, offer_date: e.target.value})}
                      />
                    </div>
                  </div>
                </div>

                {/* Fatura Detayları */}
                <div className="card p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                      <FileText className="w-4 h-4 text-primary-600" />
                      Fatura Bilgileri
                    </h3>
                    <button
                      onClick={() => {
                        if (!manualMode && result?.extraction) {
                          // OCR'dan gelen değerleri manuel forma kopyala
                          setManualValues({
                            consumption_kwh: result.extraction.consumption_kwh?.value || 0,
                            current_unit_price: result.extraction.current_active_unit_price_tl_per_kwh?.value || 0,
                            current_energy_tl: result.calculation?.current_energy_tl || 0,
                            current_distribution_tl: result.calculation?.current_distribution_tl || 0,
                            current_btv_tl: result.calculation?.current_btv_tl || 0,
                            current_vat_matrah_tl: result.calculation?.current_vat_matrah_tl || 0,
                            current_vat_tl: result.calculation?.current_vat_tl || 0,
                            current_total_with_vat_tl: result.calculation?.current_total_with_vat_tl || 0,
                            vendor: result.extraction.vendor || '',
                            invoice_period: result.extraction.invoice_period || '',
                            tariff_group: result.extraction.meta?.tariff_group_guess || 'Sanayi',
                          });
                        }
                        setManualMode(!manualMode);
                      }}
                      className={`text-xs px-2 py-1 rounded transition-colors ${
                        manualMode 
                          ? 'bg-amber-100 text-amber-700 hover:bg-amber-200' 
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      {manualMode ? '✏️ Manuel Mod' : '🔄 OCR Mod'}
                    </button>
                  </div>
                  
                  {manualMode ? (
                    /* Manuel Giriş Formu */
                    <div className="space-y-3">
                      <div className="grid grid-cols-3 gap-2">
                        <div>
                          <label className="text-xs text-gray-500 block mb-1">Tedarikçi</label>
                          <input
                            type="text"
                            className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                            value={manualValues.vendor}
                            onChange={(e) => setManualValues({...manualValues, vendor: e.target.value})}
                            placeholder="Örn: Uludağ"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-500 block mb-1">
                            Dönem
                            {priceLoading && <Loader2 className="w-3 h-3 inline ml-1 animate-spin text-primary-500" />}
                          </label>
                          <select
                            className={`w-full px-2 py-1 text-xs border rounded focus:ring-1 focus:ring-primary-500 ${
                              priceError ? 'border-red-300 bg-red-50' : 'border-gray-200'
                            } ${priceLoading ? 'animate-pulse' : ''}`}
                            value={manualValues.invoice_period}
                            onChange={(e) => setManualValues({...manualValues, invoice_period: e.target.value})}
                          >
                            <option value="">Dönem Seçin</option>
                            {PERIOD_OPTIONS.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                          {priceError && (
                            <p className="text-xs text-red-500 mt-0.5">{priceError}</p>
                          )}
                        </div>
                        <div>
                          <label className="text-xs text-gray-500 block mb-1">Tüketim (kWh)</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                            value={consumptionInput}
                            onChange={(e) => {
                              setConsumptionInput(e.target.value);
                              const parsed = parseNumber(e.target.value);
                              if (parsed > 0) {
                                setManualValues(prev => ({...prev, consumption_kwh: parsed}));
                              }
                            }}
                            onBlur={(e) => {
                              const parsed = parseNumber(e.target.value);
                              setManualValues(prev => ({...prev, consumption_kwh: parsed}));
                              if (parsed > 0) {
                                setConsumptionInput(formatNumber(parsed));
                              }
                            }}
                            placeholder="8.959,5"
                          />
                        </div>
                      </div>
                      
                      <div className="pt-2 border-t border-gray-100">
                        <p className="text-xs font-medium text-gray-700 mb-2">Mevcut Fatura Kalemleri (TL)</p>
                        <div className="grid grid-cols-4 gap-2">
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Enerji Bedeli</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500"
                              placeholder="58761,15"
                              defaultValue=""
                              onBlur={(e) => {
                                const val = parseNumber(e.target.value);
                                setManualValues({...manualValues, current_energy_tl: val});
                                e.target.value = val ? formatNumber(val) : '';
                              }}
                            />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Dağıtım Bedeli</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 bg-gray-50"
                              value={(() => {
                                const distPrice = getDistributionUnitPrice();
                                const kwh = manualValues.consumption_kwh;
                                if (distPrice > 0 && kwh > 0) {
                                  return formatNumber(distPrice * kwh);
                                }
                                return manualValues.current_distribution_tl ? formatNumber(manualValues.current_distribution_tl) : '';
                              })()}
                              readOnly
                              title="Dağıtım birim fiyatı × kWh otomatik hesaplanır"
                            />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">BTV</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 bg-gray-50"
                              value={liveCalculation?.current_btv_tl?.toLocaleString('tr-TR', {minimumFractionDigits: 2}) || ''}
                              readOnly
                            />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">KDV Matrahı</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 bg-gray-50"
                              value={liveCalculation?.current_vat_matrah_tl?.toLocaleString('tr-TR', {minimumFractionDigits: 2}) || ''}
                              readOnly
                            />
                          </div>
                        </div>
                        <div className="grid grid-cols-4 gap-2 mt-2">
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">KDV (%{Math.round(vatRate * 100)})</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 bg-gray-50"
                              value={liveCalculation?.current_vat_tl?.toLocaleString('tr-TR', {minimumFractionDigits: 2}) || ''}
                              readOnly
                            />
                          </div>
                          <div className="col-span-2">
                            <label className="text-xs text-gray-500 block mb-1">TOPLAM (KDV Dahil)</label>
                            <input
                              type="text"
                              className="w-full px-2 py-1 text-xs border border-amber-300 bg-amber-50 rounded focus:ring-1 focus:ring-amber-500 font-medium"
                              value={liveCalculation?.current_total_with_vat_tl?.toLocaleString('tr-TR', {minimumFractionDigits: 2}) || ''}
                              readOnly
                            />
                          </div>
                          <div className="flex items-end">
                            <button
                              onClick={() => {
                                // Otomatik toplam hesapla - seçilen oranlara göre
                                const btv = manualValues.current_energy_tl * btvRate;
                                const vat_matrah = manualValues.current_energy_tl + manualValues.current_distribution_tl + btv;
                                const vat = vat_matrah * vatRate;
                                setManualValues({
                                  ...manualValues,
                                  current_btv_tl: btv,
                                  current_vat_matrah_tl: vat_matrah,
                                  current_vat_tl: vat,
                                  current_total_with_vat_tl: vat_matrah + vat,
                                });
                              }}
                              className="w-full px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200"
                            >
                              Hesapla
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    /* OCR'dan Gelen Değerler (Sadece Görüntüleme) */
                    <>
                      <div className="grid grid-cols-4 gap-2 text-xs">
                        <div>
                          <p className="text-gray-500">Tedarikçi</p>
                          <p className="font-medium text-gray-900 capitalize">
                            {result.extraction.vendor || 'Bilinmiyor'}
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500">Dönem</p>
                          <p className="font-medium text-gray-900">
                            {result.extraction.invoice_period || '-'}
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500">Tüketim</p>
                          <p className="font-medium text-gray-900">
                            {result.extraction.consumption_kwh?.value?.toLocaleString('tr-TR')} kWh
                          </p>
                        </div>
                        <div>
                          <p className="text-gray-500">Birim Fiyat</p>
                          <p className="font-medium text-gray-900">
                            {result.extraction.current_active_unit_price_tl_per_kwh?.value?.toFixed(4)} TL/kWh
                          </p>
                        </div>
                      </div>
                    </>
                  )}
                  
                  {/* Dağıtım + Validasyon - Tek satır */}
                  <div className="mt-2 pt-2 border-t border-gray-100 flex items-center justify-between text-xs">
                    <div className="flex items-center gap-4">
                      <span className="text-gray-500">Dağıtım: <span className="font-medium text-gray-900">{((liveCalculation?.distribution_unit_price || 0) * 1000).toFixed(2)} kr/kWh</span></span>
                      {manualMode ? (
                        <span className="text-amber-600 font-medium">✏️ Manuel Giriş Aktif</span>
                      ) : (
                        <>
                          <span className="text-gray-500">Kaynak: 
                            {result.calculation?.meta_distribution_source?.startsWith('epdk_tariff') ? (
                              <span className="text-primary-600 ml-1">EPDK</span>
                            ) : result.calculation?.meta_distribution_source === 'extracted_from_invoice' ? (
                              <span className="text-gray-600 ml-1">Faturadan</span>
                            ) : (
                              <span className="text-amber-600 ml-1">Manuel</span>
                            )}
                          </span>
                          {result.calculation?.meta_distribution_tariff_key && (
                            <span className="text-gray-500">Tarife: <span className="font-medium">{result.calculation.meta_distribution_tariff_key}</span></span>
                          )}
                        </>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {manualMode ? (
                        manualValues.consumption_kwh > 0 && manualValues.current_total_with_vat_tl > 0 ? (
                          <>
                            <CheckCircle className="w-4 h-4 text-green-500" />
                            <span className="text-green-700 font-medium">Manuel veriler hazır</span>
                          </>
                        ) : (
                          <>
                            <AlertCircle className="w-4 h-4 text-amber-500" />
                            <span className="text-amber-700">Tüketim ve toplam giriniz</span>
                          </>
                        )
                      ) : result.validation.is_ready_for_pricing ? (
                        <>
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-green-700 font-medium">Analiz başarılı</span>
                        </>
                      ) : (
                        <>
                          <AlertCircle className="w-4 h-4 text-amber-500" />
                          <span className="text-amber-700">Eksik: {result.validation.missing_fields.join(', ')}</span>
                        </>
                      )}
                    </div>
                  </div>
                  {result.calculation?.meta_distribution_mismatch_warning && (
                    <div className="mt-1 p-1 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
                      ⚠️ {result.calculation.meta_distribution_mismatch_warning}
                    </div>
                  )}
                  
                  {/* Hesap Detayı - Debug Panel */}
                  {result.debug_meta && (
                    <details className="mt-2 pt-2 border-t border-gray-100">
                      <summary className="cursor-pointer text-xs font-medium text-gray-500 hover:text-gray-900 flex items-center gap-1">
                        <span>🔍 Hesap Detayı</span>
                        <span className="text-xs text-gray-400">(trace: {result.debug_meta.trace_id || result.meta?.trace_id})</span>
                        {result.quality_score && (
                          <span className={`ml-1 px-1 py-0.5 rounded text-xs font-medium ${
                            result.quality_score.grade === 'OK' ? 'bg-green-100 text-green-700' :
                            result.quality_score.grade === 'CHECK' ? 'bg-yellow-100 text-yellow-700' :
                            'bg-red-100 text-red-700'
                          }`}>
                            {result.quality_score.score} {result.quality_score.grade}
                          </span>
                        )}
                      </summary>
                      <div className="mt-2 p-2 bg-gray-50 rounded text-xs space-y-1">
                        {result.quality_score && result.quality_score.flags.length > 0 && (
                          <div className="pb-1 border-b border-gray-200">
                            <span className="text-gray-600 font-medium">Bayraklar:</span>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {result.quality_score.flag_details.map((flag, i) => (
                                <span key={i} className={`inline-flex items-center px-1 py-0.5 rounded text-xs ${
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
                        
                        <div className="grid grid-cols-4 gap-1">
                          <div><span className="text-gray-500">Dönem:</span> <span className="font-mono">{result.debug_meta.pricing_period || result.calculation?.meta_pricing_period || '-'}</span></div>
                          <div><span className="text-gray-500">Kaynak:</span> <span className={`font-medium ${result.debug_meta.pricing_source === 'reference' ? 'text-primary-600' : 'text-amber-600'}`}>{result.debug_meta.pricing_source || '-'}</span></div>
                          <div><span className="text-gray-500">PTF:</span> <span className="font-mono">{result.debug_meta.ptf_tl_per_mwh || 0}</span></div>
                          <div><span className="text-gray-500">YEKDEM:</span> <span className="font-mono">{result.debug_meta.yekdem_tl_per_mwh || 0}</span></div>
                        </div>
                        
                        {result.debug_meta.warnings && result.debug_meta.warnings.length > 0 && (
                          <div className="pt-1 border-t border-gray-200 text-amber-600">
                            ⚠️ {result.debug_meta.warnings.join(', ')}
                          </div>
                        )}
                      </div>
                    </details>
                  )}
                </div>

                {/* Karşılaştırma Tablosu */}
                <div className="card p-3">
                  <div className="flex justify-between items-center mb-2">
                    <h3 className="text-sm font-semibold text-gray-900">
                      Detaylı Karşılaştırma
                    </h3>
                    <button
                      onClick={handleDownloadPdf}
                      disabled={pdfLoading}
                      className="btn-primary flex items-center gap-1 px-3 py-1 text-xs"
                    >
                      {pdfLoading ? (
                        <>
                          <Loader2 className="w-3 h-3 animate-spin" />
                          PDF...
                        </>
                      ) : (
                        <>
                          <Download className="w-3 h-3" />
                          PDF İndir
                        </>
                      )}
                    </button>
                  </div>
                  
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th className="text-left py-1 px-2 font-medium text-gray-500">Kalem</th>
                        <th className="text-right py-1 px-2 font-medium text-gray-500">Mevcut</th>
                        <th className="text-right py-1 px-2 font-medium text-gray-500">Teklif</th>
                        <th className="text-right py-1 px-2 font-medium text-gray-500">Fark</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      <tr>
                        <td className="py-1 px-2 text-gray-700">Enerji Bedeli</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_energy_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.offer_energy_tl)}</td>
                        <td className={`py-1 px-2 text-right font-medium ${liveCalculation.current_energy_tl > liveCalculation.offer_energy_tl ? 'text-green-600' : 'text-red-600'}`}>
                          {formatCurrency(liveCalculation.current_energy_tl - liveCalculation.offer_energy_tl)}
                        </td>
                      </tr>
                      <tr>
                        <td className="py-1 px-2 text-gray-700">Dağıtım Bedeli</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_distribution_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.offer_distribution_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-500">-</td>
                      </tr>
                      <tr>
                        <td className="py-1 px-2 text-gray-700">BTV</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_btv_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.offer_btv_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-500">-</td>
                      </tr>
                      <tr>
                        <td className="py-1 px-2 text-gray-700">KDV Matrahı</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_vat_matrah_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.offer_vat_matrah_tl)}</td>
                        <td className={`py-1 px-2 text-right font-medium ${liveCalculation.difference_excl_vat_tl > 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {formatCurrency(liveCalculation.difference_excl_vat_tl)}
                        </td>
                      </tr>
                      <tr>
                        <td className="py-1 px-2 text-gray-700">KDV (%{Math.round(vatRate * 100)})</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_vat_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.offer_vat_tl)}</td>
                        <td className="py-1 px-2 text-right text-gray-500">-</td>
                      </tr>
                      <tr className="bg-gray-50 font-semibold">
                        <td className="py-1 px-2 text-gray-900">TOPLAM</td>
                        <td className="py-1 px-2 text-right text-gray-900">{formatCurrency(liveCalculation.current_total_with_vat_tl)}</td>
                        <td className="py-1 px-2 text-right text-primary-600">{formatCurrency(liveCalculation.offer_total_with_vat_tl)}</td>
                        <td className={`py-1 px-2 text-right ${liveCalculation.difference_incl_vat_tl > 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {formatCurrency(liveCalculation.difference_incl_vat_tl)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                {/* Aksiyon Butonları */}
                <div className="flex gap-2">
                  <button
                    onClick={handleDownloadPdf}
                    disabled={pdfLoading}
                    className="btn-primary flex-1 flex items-center justify-center gap-2 py-2 text-sm"
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
                    Fatura Yükleyin veya Manuel Giriş Yapın
                  </h3>
                  <p className="text-gray-500 max-w-sm mb-4">
                    Elektrik faturanızı yükleyin veya değerleri manuel olarak girerek teklif oluşturun.
                  </p>
                  <button
                    onClick={() => {
                      setManualMode(true);
                      setManualValues({
                        consumption_kwh: 0,
                        current_unit_price: 0,
                        current_energy_tl: 0,
                        current_distribution_tl: 0,
                        current_btv_tl: 0,
                        current_vat_matrah_tl: 0,
                        current_vat_tl: 0,
                        current_total_with_vat_tl: 0,
                        vendor: '',
                        invoice_period: '',
                        tariff_group: 'Sanayi OG',
                      });
                      // Boş bir result oluştur ki UI gösterilsin
                      setResult({
                        extraction: {
                          vendor: '',
                          invoice_period: '',
                          consumption_kwh: { value: 0, confidence: 0 },
                        },
                        validation: { is_ready_for_pricing: false, missing_fields: [] },
                        calculation: null,
                      } as any);
                    }}
                    className="btn-secondary inline-flex items-center gap-2"
                  >
                    <FileText className="w-4 h-4" />
                    Manuel Teklif Oluştur
                  </button>
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
