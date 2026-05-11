import { useState, useCallback, useMemo, useEffect } from 'react';
import { Upload, FileText, Zap, TrendingDown, AlertCircle, CheckCircle, Loader2, RefreshCw, Download, Settings } from 'lucide-react';
import { fullProcess, downloadPdf, FullProcessResponse, pricingAnalyze, pricingGetTemplates, pricingDownloadPdf, pricingDownloadExcel, PricingAnalyzeResponse, normalizeInvoicePeriod, API_BASE, TemplateItem } from './api';
import AdminPanel from './AdminPanel';
import { generateBayiRaporPdf } from './bayiRapor';

// EPDK Dağıtım Tarifeleri — Backend API'den çekilir
// localStorage cache ile 24 saat TTL
type TariffEntry = { key: string; label: string; price: number; group: string };

const DIST_TARIFF_CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 saat
const DIST_TARIFF_CACHE_VERSION = 'v4'; // Çerkezköy OSB 0.604 düzeltildi

// localStorage cache'den tarife oku (TTL kontrolü ile)
function getCachedTariffs(period: string): TariffEntry[] | null {
  try {
    const raw = localStorage.getItem(`dist_tariffs_${DIST_TARIFF_CACHE_VERSION}_${period}`);
    if (!raw) return null;
    const cached = JSON.parse(raw);
    if (Date.now() - cached.timestamp > DIST_TARIFF_CACHE_TTL_MS) {
      localStorage.removeItem(`dist_tariffs_${DIST_TARIFF_CACHE_VERSION}_${period}`);
      return null;
    }
    return cached.tariffs;
  } catch {
    return null;
  }
}

// localStorage cache'e tarife yaz
function setCachedTariffs(period: string, tariffs: TariffEntry[]) {
  try {
    localStorage.setItem(`dist_tariffs_${DIST_TARIFF_CACHE_VERSION}_${period}`, JSON.stringify({
      timestamp: Date.now(),
      tariffs,
    }));
  } catch { /* localStorage dolu olabilir — yoksay */ }
}

// Backend API'den tarifeleri çek, cache + fallback ile
async function fetchDistributionTariffs(period: string | null): Promise<TariffEntry[]> {
  const cacheKey = period || '__current__';
  // 1. Cache kontrol
  const cached = getCachedTariffs(cacheKey);
  if (cached) return cached;

  // 2. API çağrısı
  try {
    const url = period
      ? `${API_BASE}/api/pricing/distribution-tariffs?period=${encodeURIComponent(period)}`
      : `${API_BASE}/api/pricing/distribution-tariffs`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const tariffs: TariffEntry[] = (data.tariffs || []).map((t: any) => ({
      key: t.key,
      label: t.label,
      price: t.unit_price_tl_per_kwh,
      group: t.tariff_group,
    }));
    // Manuel giriş seçeneği ekle
    tariffs.push({ key: 'custom', label: 'Manuel Giriş', price: 0, group: 'custom' });
    setCachedTariffs(cacheKey, tariffs);
    return tariffs;
  } catch {
    // 3. Fallback: son bilinen tarife
    const fallback = getCachedTariffs(cacheKey);
    if (fallback) return fallback;
    // 4. Hardcoded fallback — EPDK Nisan 2026 güncel tarifeler (kr/kWh → TL/kWh)
    return [
      { key: 'sanayi_og_cift_terim', label: 'Sanayi OG Çift Terim', price: 1.07050, group: 'sanayi' },
      { key: 'sanayi_og_tek_terim', label: 'Sanayi OG Tek Terim', price: 1.18246, group: 'sanayi' },
      { key: 'sanayi_ag_cift_terim', label: 'Sanayi AG Çift Terim', price: 1.58400, group: 'sanayi' },
      { key: 'sanayi_ag_tek_terim', label: 'Sanayi AG Tek Terim', price: 1.82950, group: 'sanayi' },
      { key: 'ticarethane_og_cift_terim', label: 'Ticarethane OG Çift Terim', price: 1.66835, group: 'ticarethane' },
      { key: 'ticarethane_og_tek_terim', label: 'Ticarethane OG Tek Terim', price: 2.08106, group: 'ticarethane' },
      { key: 'ticarethane_ag_cift_terim', label: 'Ticarethane AG Çift Terim', price: 2.17800, group: 'ticarethane' },
      { key: 'ticarethane_ag_tek_terim', label: 'Ticarethane AG Tek Terim', price: 2.47936, group: 'ticarethane' },
      { key: 'mesken_ag_tek_terim', label: 'Mesken AG Tek Terim', price: 2.42490, group: 'mesken' },
      { key: 'tarimsal_og_cift_terim', label: 'Tarımsal OG Çift Terim', price: 1.37400, group: 'tarimsal' },
      { key: 'tarimsal_ag_tek_terim', label: 'Tarımsal AG Tek Terim', price: 2.03600, group: 'tarimsal' },
      { key: 'aydinlatma_ag_tek_terim', label: 'Aydınlatma AG Tek Terim', price: 2.37600, group: 'aydinlatma' },
      { key: 'osb_cerkezkoy', label: 'Çerkezköy OSB', price: 0.60400, group: 'osb_cerkezkoy' },
      { key: 'osb_ikitelli', label: 'İkitelli OSB', price: 0.81053, group: 'osb_ikitelli' },
      { key: 'custom', label: 'Manuel Giriş', price: 0, group: 'custom' },
    ];
  }
}

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

// ═══════════════════════════════════════════════════════════════════════════════
// Bayi Komisyon Segmentleri — PUAN PAYLAŞIMI MODELİ
// ═══════════════════════════════════════════════════════════════════════════════
// Katsayı 1.10 → 10 puan kar. Bayi segmente göre sabit puan alır.
// Bayi Komisyonu = Baz Enerji × (bayiPuan / 100)
// Gelka Net = Baz Enerji × ((toplam puan - bayiPuan) / 100)
//
// Örnek: katsayı 1.15, segment "Yüksek" (3p bayi)
//   Toplam marj puanı: 15
//   Bayi: 3 puan → Baz Enerji × 0.03
//   Gelka: 12 puan → Baz Enerji × 0.12

interface BayiSegment {
  name: string;
  minMultiplier: number;
  maxMultiplier: number;
  bayiPoints: number; // Bayinin aldığı sabit puan
  requiresApproval: boolean;
}

const BAYI_SEGMENTS: BayiSegment[] = [
  { name: 'Özel Onay',   minMultiplier: 1.01, maxMultiplier: 1.03, bayiPoints: 0,   requiresApproval: true },
  { name: 'Sabit',       minMultiplier: 1.03, maxMultiplier: 1.06, bayiPoints: 1,   requiresApproval: false },
  { name: 'Artırılmış',  minMultiplier: 1.06, maxMultiplier: 1.09, bayiPoints: 1.5, requiresApproval: false },
  { name: 'Yüksek',      minMultiplier: 1.09, maxMultiplier: 1.12, bayiPoints: 2,   requiresApproval: false },
  { name: 'Yüksek+',     minMultiplier: 1.12, maxMultiplier: 1.15, bayiPoints: 3,   requiresApproval: false },
  { name: 'Premium',     minMultiplier: 1.15, maxMultiplier: 99,   bayiPoints: 4,   requiresApproval: false },
];

function getBayiSegment(multiplier: number): BayiSegment | null {
  if (multiplier < 1.01) return null;
  return BAYI_SEGMENTS.find(s => multiplier >= s.minMultiplier && multiplier < s.maxMultiplier) || null;
}

// Risk Level Türkçe etiket mapping
const RISK_LEVEL_LABELS: Record<string, string> = {
  'low': 'Düşük',
  'medium': 'Orta',
  'high': 'Yüksek',
  'very_high': 'Çok Yüksek',
};

function calculateBayiCommission(
  multiplier: number,
  baseEnergyTl: number,   // kWh × (PTF + YEKDEM) / 1000 — katsayı HARİÇ
  customPoints?: number,   // Özel onay segmenti için manuel puan
): {
  segment: BayiSegment | null;
  commission_tl: number;
  bayiPoints: number;
  gelkaPoints: number;
  totalMarginPoints: number;
  requiresApproval: boolean;
} {
  const segment = getBayiSegment(multiplier);
  if (!segment) return { segment: null, commission_tl: 0, bayiPoints: 0, gelkaPoints: 0, totalMarginPoints: 0, requiresApproval: false };
  
  const totalMarginPoints = (multiplier - 1) * 100; // 1.10 → 10 puan
  const bayiPoints = segment.requiresApproval ? (customPoints || 0) : segment.bayiPoints;
  const gelkaPoints = totalMarginPoints - bayiPoints;
  
  // Komisyon = Baz Enerji × (bayiPuan / 100)
  const commission_tl = baseEnergyTl * (bayiPoints / 100);
  
  return { segment, commission_tl, bayiPoints, gelkaPoints, totalMarginPoints, requiresApproval: segment.requiresApproval };
}

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
  
  // Bayi komisyonu (toplam marjın yüzdesi olarak, örn: 25 = %25)
  // Çarpan 1.06 ve bayi %25 ise: marj=6p, bayi=6×0.25=1.5p, gelka=4.5p
  // PDF'e yansımaz, sadece dahili kar hesabı için
  const [bayiEnabled, setBayiEnabled] = useState(false);
  const [bayiOzelOnayPuan, setBayiOzelOnayPuan] = useState(0.5); // Özel onay segmenti için varsayılan 0.5 puan // Özel onay segmenti için %0.5 varsayılan
  
  // ── Risk Analizi Paneli State'leri ──
  const [riskPanelEnabled, setRiskPanelEnabled] = useState(false);
  const [riskLoading, setRiskLoading] = useState(false);
  const [riskError, setRiskError] = useState<string | null>(null);
  const [riskResult, setRiskResult] = useState<PricingAnalyzeResponse | null>(null);
  const [marginDetailOpen, setMarginDetailOpen] = useState(false);
  const [riskTemplateName, setRiskTemplateName] = useState('3_vardiya_sanayi');
  const [riskTemplates, setRiskTemplates] = useState<TemplateItem[]>([]);
  
  // ── T1/T2/T3 Giriş Modu State'leri ──
  type InputMode = 'template' | 't1t2t3';
  const [inputMode, setInputMode] = useState<InputMode>('template');
  const [t1Kwh, setT1Kwh] = useState<number>(0);
  const [t2Kwh, setT2Kwh] = useState<number>(0);
  const [t3Kwh, setT3Kwh] = useState<number>(0);
  const [voltageLevel, setVoltageLevel] = useState<'ag' | 'og'>('og');
  const totalT1T2T3 = t1Kwh + t2Kwh + t3Kwh;
  const allT1T2T3Zero = totalT1T2T3 === 0;
  
  // PTF/YEKDEM kaynağı: true = DB'den otomatik, false = manuel override
  const [useReferencePrices, setUseReferencePrices] = useState(true);
  
  // PTF/YEKDEM fiyat çekme durumu
  const [priceLoading, setPriceLoading] = useState(false);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [priceSaving, setPriceSaving] = useState(false);
  const [priceSaved, setPriceSaved] = useState(false);
  const [priceModified, setPriceModified] = useState(false); // kullanıcı elle değiştirdi mi
  
  // Dağıtım birim fiyatı (manuel override)
  const [distributionTariffKey, setDistributionTariffKey] = useState<string>('');
  const [customDistributionPrice, setCustomDistributionPrice] = useState<number>(0);
  
  // Teklif birim fiyat gösterim modu
  // 'energy': (PTF + YEKDEM) × Katsayı — sadece enerji (default)
  // 'combined': (PTF + YEKDEM) × Katsayı + Dağıtım — toplam kWh fiyatı
  // 'detailed': PTF × Katsayı ayrı, YEKDEM ayrı satır
  const [offerDisplayMode, setOfferDisplayMode] = useState<'energy' | 'combined' | 'detailed'>('energy');
  
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
  const [currentUnitPriceInput, setCurrentUnitPriceInput] = useState('');
  const [manualValues, setManualValues] = useState({
    consumption_kwh: 0,
    current_unit_price: 0,  // Mevcut tedarikçinin birim aktif enerji fiyatı (TL/kWh)
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
    { value: 1.01, label: '1.01 (%1)' },
    { value: 1.03, label: '1.03 (%3)' },
    { value: 1.06, label: '1.06 (%6)' },
    { value: 1.09, label: '1.09 (%9)' },
    { value: 1.12, label: '1.12 (%12)' },
    { value: 1.15, label: '1.15 (%15)' },
    { value: 1.19, label: '1.19 (%19)' },
    { value: 1.23, label: '1.23 (%23)' },
  ];
  
  // Seçili döneme göre aktif EPDK tarifeleri — backend API'den çekilir
  const selectedPeriod = manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '');
  const [activeTariffs, setActiveTariffs] = useState<TariffEntry[]>([{ key: 'custom', label: 'Manuel Giriş', price: 0, group: 'custom' }]);
  const [tariffWarning, setTariffWarning] = useState<string | null>(null);

  // Dönem değiştiğinde tarifeleri API'den çek (cache + fallback)
  useEffect(() => {
    let cancelled = false;
    setTariffWarning(null);
    fetchDistributionTariffs(selectedPeriod || null).then(tariffs => {
      if (!cancelled) {
        setActiveTariffs(tariffs);
        // Eğer sadece 'custom' varsa API'den veri gelmemiş demektir
        if (tariffs.length <= 1) {
          setTariffWarning('Dağıtım tarifeleri yüklenemedi — manuel giriş kullanın.');
        }
      }
    });
    return () => { cancelled = true; };
  }, [selectedPeriod]);
  
  // Dağıtım birim fiyatını belirle (öncelik: manuel > tarife seçimi > backend)
  const getDistributionUnitPrice = useCallback(() => {
    if (distributionTariffKey === 'custom') {
      return customDistributionPrice;
    }
    if (distributionTariffKey && distributionTariffKey !== 'custom') {
      const tariff = activeTariffs.find(t => t.key === distributionTariffKey);
      if (tariff) return tariff.price;
    }
    // Backend'den gelen değer
    return result?.extraction?.distribution_unit_price_tl_per_kwh?.value || 0;
  }, [distributionTariffKey, customDistributionPrice, result?.extraction?.distribution_unit_price_tl_per_kwh?.value, activeTariffs]);
  
  // Parametreler değiştiğinde otomatik yeniden hesaplama
  // Backend'den gelen calculation varsa onu kullan, yoksa frontend'de hesapla
  const liveCalculation = useMemo(() => {
    // Manuel mod aktifse, manuel değerlerle hesapla
    if (manualMode && manualValues.consumption_kwh > 0) {
      const kwh = manualValues.consumption_kwh;
      const distUnitPrice = getDistributionUnitPrice() || (manualValues.current_distribution_tl / kwh);
      
      const ptfKwh = ptfPrice / 1000;
      const yekdemKwh = yekdemPrice / 1000;
      
      // Mevcut fatura değerleri: Mevcut tedarikçinin birim fiyatı × kWh
      // current_unit_price = tedarikçinin uyguladığı birim aktif enerji fiyatı (TL/kWh)
      // Bu, EPİAŞ PTF'den farklıdır — tedarikçi kendi marjını, risk primini vs. ekler
      const current_energy_tl = manualValues.current_unit_price > 0
        ? manualValues.current_unit_price * kwh
        : manualValues.current_energy_tl;
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
      
      // Bayi komisyonu — Puan paylaşımı modeli
      // offer_energy_base = kWh × (PTF + YEKDEM) — baz enerji bedeli
      const bayiResult = bayiEnabled 
        ? calculateBayiCommission(multiplier, offer_energy_base, bayiOzelOnayPuan)
        : { segment: null, commission_tl: 0, bayiPoints: 0, gelkaPoints: 0, totalMarginPoints: 0, requiresApproval: false };
      const bayi_commission_tl = bayiResult.commission_tl;
      const gelka_net_profit_tl = supplier_profit_tl - bayi_commission_tl;
      const bayiPuanHesap = bayiResult.bayiPoints;
      
      // Dual margin hesaplama (v3)
      const gross_margin_energy = offer_energy_tl - (kwh * offerBasePrice);
      const gross_margin_total = gross_margin_energy - offer_distribution_tl;
      const net_margin = gross_margin_total - bayi_commission_tl;

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
        bayi_commission_tl,
        gelka_net_profit_tl,
        bayi_puan_hesap: bayiPuanHesap,
        include_yekdem: includeYekdem,
        distribution_unit_price: distUnitPrice,
        bayi_segment: bayiResult.segment,
        bayi_rate: bayiResult.bayiPoints / 100,
        bayi_requires_approval: bayiResult.requiresApproval,
        bayi_commission_base_tl: offer_energy_base,
        bayi_points: bayiResult.bayiPoints,
        gelka_points: bayiResult.gelkaPoints,
        total_margin_points: bayiResult.totalMarginPoints,
        // Dual margin (v3)
        gross_margin_energy_tl: gross_margin_energy,
        gross_margin_total_tl: gross_margin_total,
        net_margin_tl: net_margin,
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
    
    // Mevcut fatura: Backend'den gelen değerleri kullan (faturadan okunan SOURCE OF TRUTH)
    // BTV ve KDV backend'den geldiyse onu kullan, yoksa frontend'de hesapla
    const current_btv_tl = backendCalc?.current_btv_tl ?? (current_energy_tl * btvRate);
    const current_vat_matrah_tl = backendCalc?.current_vat_matrah_tl ?? (current_energy_tl + current_distribution_tl + current_btv_tl);
    const current_vat_tl = backendCalc?.current_vat_tl ?? (current_vat_matrah_tl * vatRate);
    // Faturadan okunan toplam (SOURCE OF TRUTH) - backend hesaplamasını kullan
    const current_total_with_vat_tl = backendCalc?.current_total_with_vat_tl ?? (current_vat_matrah_tl + current_vat_tl);
    
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
    
    // Bayi komisyonu — Puan paylaşımı modeli
    const bayiResult = bayiEnabled 
      ? calculateBayiCommission(multiplier, offer_energy_base, bayiOzelOnayPuan)
      : { segment: null, commission_tl: 0, bayiPoints: 0, gelkaPoints: 0, totalMarginPoints: 0, requiresApproval: false };
    const bayi_commission_tl = bayiResult.commission_tl;
    const gelka_net_profit_tl = supplier_profit_tl - bayi_commission_tl;
    const bayiPuanHesap = bayiResult.bayiPoints;
    
    // Dual margin hesaplama (v3)
    const gross_margin_energy = offer_energy_tl - offer_energy_base;
    const gross_margin_total = gross_margin_energy - offer_distribution_tl;
    const net_margin = gross_margin_total - bayi_commission_tl;

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
      bayi_commission_tl,
      gelka_net_profit_tl,
      bayi_puan_hesap: bayiPuanHesap,
      include_yekdem: includeYekdem,  // UI'da göstermek için
      distribution_unit_price: distUnitPrice,  // UI'da göstermek için
      bayi_segment: bayiResult.segment,
      bayi_rate: bayiResult.bayiPoints / 100,
      bayi_requires_approval: bayiResult.requiresApproval,
      bayi_commission_base_tl: offer_energy_base,
      bayi_points: bayiResult.bayiPoints,
      gelka_points: bayiResult.gelkaPoints,
      total_margin_points: bayiResult.totalMarginPoints,
      // Dual margin (v3)
      gross_margin_energy_tl: gross_margin_energy,
      gross_margin_total_tl: gross_margin_total,
      net_margin_tl: net_margin,
    };
  }, [result?.extraction, result?.calculation, ptfPrice, yekdemPrice, multiplier, getDistributionUnitPrice, manualMode, manualValues, btvRate, vatRate, bayiEnabled, bayiOzelOnayPuan]);

  // ── Risk Buffer hesaplama — türetilen değerler (state değil) ──
  const selectedTemplate = useMemo(
    () => riskTemplates.find(t => t.name === riskTemplateName) ?? null,
    [riskTemplates, riskTemplateName]
  );
  const baseMarginPct = (multiplier - 1) * 100;
  const riskBufferPct = selectedTemplate?.risk_buffer_pct ?? 0;
  const recommendedMarginPct = baseMarginPct + riskBufferPct;

  // ── Risk Paneli: Dönem ve şablon listesi çek ──
  useEffect(() => {
    if (!riskPanelEnabled) return;
    pricingGetTemplates()
      .then(res => setRiskTemplates(res.items || []))
      .catch(() => setRiskTemplates([]));
  }, [riskPanelEnabled]);

  // ── Risk Paneli: Auto-trigger (debounce 500ms) ──
  useEffect(() => {
    if (!riskPanelEnabled) return;
    // T1/T2/T3 modunda tümü sıfırsa tetikleme
    if (inputMode === 't1t2t3' && totalT1T2T3 === 0) return;
    const timer = setTimeout(() => {
      runRiskAnalysis();
    }, 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [riskPanelEnabled, multiplier, riskTemplateName, manualValues.invoice_period, manualValues.consumption_kwh, bayiEnabled, inputMode, t1Kwh, t2Kwh, t3Kwh, voltageLevel]);

  // ── Risk Paneli: Analiz çalıştır ──
  const runRiskAnalysis = useCallback(async () => {
    const period = manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '');
    const normalizedPeriod = period ? normalizeInvoicePeriod(period) : null;
    if (!normalizedPeriod) {
      setRiskError('Dönem bilgisi bulunamadı.');
      return;
    }
    
    // T1/T2/T3 modunda toplam sıfırsa çalıştırma
    if (inputMode === 't1t2t3') {
      if (totalT1T2T3 <= 0) {
        setRiskError('En az bir zaman diliminde tüketim giriniz.');
        return;
      }
    } else {
      const kwh = manualMode ? manualValues.consumption_kwh : (result?.extraction?.consumption_kwh?.value || 0);
      if (kwh <= 0) {
        setRiskError('Tüketim bilgisi bulunamadı.');
        return;
      }
    }

    setRiskLoading(true);
    setRiskError(null);
    setRiskResult(null);

    try {
      const kwh = manualMode ? manualValues.consumption_kwh : (result?.extraction?.consumption_kwh?.value || 0);
      
      // T1/T2/T3 modunda farklı parametreler gönder
      const reqParams: any = {
        period: normalizedPeriod,
        multiplier: multiplier,
        dealer_commission_pct: bayiEnabled ? (getBayiSegment(multiplier)?.bayiPoints || 0) : 0,
        imbalance_params: {
          forecast_error_rate: 0.05,
          imbalance_cost_tl_per_mwh: 50.0,
          smf_based_imbalance_enabled: false,
        },
      };

      if (inputMode === 't1t2t3') {
        reqParams.use_template = false;
        reqParams.t1_kwh = t1Kwh;
        reqParams.t2_kwh = t2Kwh;
        reqParams.t3_kwh = t3Kwh;
        reqParams.template_monthly_kwh = totalT1T2T3;
        reqParams.voltage_level = voltageLevel;
      } else {
        reqParams.use_template = true;
        reqParams.template_name = riskTemplateName;
        reqParams.template_monthly_kwh = kwh;
        reqParams.voltage_level = voltageLevel;
      }

      const res = await pricingAnalyze(reqParams);
      setRiskResult(res);
    } catch (err: any) {
      setRiskError(err.message || 'Risk analizi başarısız.');
    } finally {
      setRiskLoading(false);
    }
  }, [manualMode, manualValues, result, multiplier, bayiEnabled, riskTemplateName, inputMode, t1Kwh, t2Kwh, t3Kwh, totalT1T2T3, voltageLevel]);

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

  // Enerji bedeli otomatik hesaplama: Mevcut birim fiyat × kWh
  useEffect(() => {
    if (!manualMode) return;
    const kwh = manualValues.consumption_kwh;
    const unitPrice = manualValues.current_unit_price;
    if (kwh > 0 && unitPrice > 0) {
      const autoVal = unitPrice * kwh;
      if (Math.abs(manualValues.current_energy_tl - autoVal) > 0.01) {
        setManualValues(prev => ({...prev, current_energy_tl: autoVal}));
      }
    }
  }, [manualMode, manualValues.consumption_kwh, manualValues.current_unit_price]);

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
        // DB'den PTF/YEKDEM fiyatlarını çek (auto_fetch=true ile EPİAŞ fallback aktif)
        const res = await fetch(`${API_BASE}/api/epias/prices/${period}`);
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error(errBody.detail || `HTTP ${res.status}`);
        }
        const response = await res.json();
        
        if (response.ptf_tl_per_mwh !== undefined && response.ptf_tl_per_mwh !== null) {
          setPtfPrice(response.ptf_tl_per_mwh);
        }
        if (response.yekdem_tl_per_mwh !== undefined && response.yekdem_tl_per_mwh !== null) {
          setYekdemPrice(response.yekdem_tl_per_mwh);
        }
        setPriceModified(false);
        setPriceSaved(false);
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
        vat_rate: vatRate,
        btv_rate: btvRate,
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
    setConsumptionInput('');
    setCurrentUnitPriceInput('');
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
      ? activeTariffs.find(t => t.key === distributionTariffKey)?.label || manualValues.tariff_group
      : manualValues.tariff_group;
    
    // Manuel modda veya OCR modda extraction oluştur
    const distUnitForPdf = liveCalculation.distribution_unit_price || 0;
    const pdfCurrentUnit = offerDisplayMode === 'combined' 
      ? manualValues.current_unit_price + distUnitForPdf 
      : manualValues.current_unit_price;
    const pdfDistUnit = offerDisplayMode === 'combined' ? 0 : distUnitForPdf;
    
    const extraction: any = manualMode ? {
      vendor: manualValues.vendor || 'Manuel Giriş',
      invoice_period: manualValues.invoice_period || '-',
      consumption_kwh: { value: manualValues.consumption_kwh, confidence: 1.0 },
      current_active_unit_price_tl_per_kwh: { value: pdfCurrentUnit, confidence: 1.0 },
      distribution_unit_price_tl_per_kwh: { value: pdfDistUnit, confidence: 1.0 },
      meta: { tariff_group_guess: selectedTariffLabel || 'Sanayi' },
    } : {
      ...result?.extraction,
      meta: { 
        ...(result?.extraction as any)?.meta,
        tariff_group_guess: selectedTariffLabel || (result?.extraction as any)?.meta?.tariff_group_guess || 'Sanayi' 
      },
    };
    
    if (!extraction) return;
    
    setPdfLoading(true);
    try {
      const tariffLabel = distributionTariffKey 
        ? activeTariffs.find(t => t.key === distributionTariffKey)?.label 
        : manualValues.tariff_group || 'Sanayi';
      
      const companySlug = customerInfo.company_name
        ? customerInfo.company_name.trim().replace(/\s+/g, '_').replace(/[^a-zA-Z0-9_çÇğĞıİöÖşŞüÜ]/g, '')
        : '';
      const now = new Date();
      const offerId = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}${String(now.getSeconds()).padStart(2,'0')}`;
      const fileName = companySlug
        ? `teklif_${companySlug}_${offerId}.pdf`
        : `teklif_${offerId}.pdf`;
      
      await downloadPdf(
        extraction,
        {
          current_energy_tl: offerDisplayMode === 'combined' 
            ? liveCalculation.current_energy_tl + liveCalculation.current_distribution_tl 
            : liveCalculation.current_energy_tl,
          current_distribution_tl: offerDisplayMode === 'combined' ? 0 : liveCalculation.current_distribution_tl,
          current_btv_tl: liveCalculation.current_btv_tl,
          current_vat_matrah_tl: liveCalculation.current_vat_matrah_tl,
          current_vat_tl: liveCalculation.current_vat_tl,
          current_total_with_vat_tl: liveCalculation.current_total_with_vat_tl,
          offer_energy_tl: offerDisplayMode === 'combined'
            ? liveCalculation.offer_energy_tl + liveCalculation.offer_distribution_tl
            : liveCalculation.offer_energy_tl,
          offer_distribution_tl: offerDisplayMode === 'combined' ? 0 : liveCalculation.offer_distribution_tl,
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
        fileName,
        customerInfo.company_name || undefined,
        customerInfo.contact_person || undefined,
        customerInfo.offer_date || undefined,
        customerInfo.offer_validity_days || 15,
        tariffLabel || 'Sanayi'
      );
    } catch (err: any) {
      console.error('PDF Download Error:', err);
      const errorMsg = err.message || 'PDF oluşturulurken hata oluştu.';
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
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex flex-col overflow-auto">
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

      <main className="flex-1 max-w-7xl mx-auto px-4 py-3 w-full overflow-auto">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Sol Panel - Yükleme ve Parametreler */}
          <div className="lg:col-span-1 space-y-2 overflow-auto">
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
                      Teklif PTF (TL/MWh)
                      {priceLoading && <Loader2 className="w-3 h-3 inline ml-1 animate-spin text-primary-500" />}
                    </label>
                    {(() => {
                      const ptfDisabled = false; // Her zaman düzenlenebilir — dönem seçildiğinde otomatik dolar ama sonra elle değiştirilebilir
                      const ptfPresets = [
                        { label: 'Oca 25 — 2.508,80', value: 2508.80 },
                        { label: 'Şub 25 — 2.478,28', value: 2478.28 },
                        { label: 'Mar 25 — 2.183,83', value: 2183.83 },
                        { label: 'Nis 25 — 2.452,67', value: 2452.67 },
                        { label: 'May 25 — 2.458,15', value: 2458.15 },
                        { label: 'Haz 25 — 2.202,23', value: 2202.23 },
                        { label: 'Tem 25 — 2.965,16', value: 2965.16 },
                        { label: 'Ağu 25 — 2.939,24', value: 2939.24 },
                        { label: 'Eyl 25 — 2.729,02', value: 2729.02 },
                        { label: 'Eki 25 — 2.739,50', value: 2739.50 },
                        { label: 'Kas 25 — 2.784,10', value: 2784.10 },
                        { label: 'Ara 25 — 2.973,04', value: 2973.04 },
                        { label: 'Oca 26 — 2.894,92', value: 2894.92 },
                        { label: 'Şub 26 — 2.078,20', value: 2078.20 },
                        { label: 'Mar 26 — 1.620,32', value: 1620.32 },
                        { label: 'Nis 26 — 1.038,80', value: 1038.80 },
                      ];
                      return (
                        <div className="relative">
                          <input
                            type="number"
                            className={`w-full px-2 py-1.5 pr-7 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none ${ptfDisabled ? 'bg-gray-50' : ''} ${priceLoading ? 'animate-pulse' : ''}`}
                            value={ptfPrice || ''}
                            onChange={(e) => { setPtfPrice(e.target.value === '' ? 0 : parseFloat(e.target.value)); setPriceModified(true); setPriceSaved(false); }}
                            onFocus={(e) => { if (ptfPrice === 0) e.target.value = ''; }}
                            step="0.1"
                            disabled={ptfDisabled}
                          />
                          {!ptfDisabled && (
                            <div className="absolute right-0 top-0 bottom-0 w-7 flex items-center justify-center">
                              <select
                                className="absolute inset-0 opacity-0 cursor-pointer"
                                value=""
                                onChange={(e) => {
                                  if (e.target.value) {
                                    setPtfPrice(parseFloat(e.target.value));
                                    setPriceModified(true); setPriceSaved(false);
                                  }
                                }}
                              >
                                <option value="">EPİAŞ PTF</option>
                                {ptfPresets.map(p => (
                                  <option key={p.value} value={p.value.toString()}>{p.label}</option>
                                ))}
                              </select>
                              <div className="pointer-events-none text-gray-400">
                                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                  <div>
                    {(() => {
                      const yekdemDisabled = !!(liveCalculation && !liveCalculation.include_yekdem && !manualMode); // YEKDEM yoksa disabled, ama referans modunda bile düzenlenebilir
                      const yekdemVal = liveCalculation && !liveCalculation.include_yekdem && !manualMode ? 0 : yekdemPrice;
                      const yekdemPresets = [
                        { label: 'Oca 26 — 162,73', value: 162.73 },
                        { label: 'Şub 26 — 479,35', value: 479.35 },
                        { label: 'Mar 26 — 747,80', value: 747.80 },
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
                              value={yekdemVal || ''}
                              onChange={(e) => { setYekdemPrice(e.target.value === '' ? 0 : parseFloat(e.target.value)); setPriceModified(true); setPriceSaved(false); }}
                              onFocus={(e) => { if (yekdemVal === 0) e.target.value = ''; }}
                              step="0.1"
                              disabled={yekdemDisabled}
                            />
                            {!yekdemDisabled && (
                              <div className="absolute right-0 top-0 bottom-0 w-7 flex items-center justify-center">
                                <select
                                  className="absolute inset-0 opacity-0 cursor-pointer"
                                  value=""
                                  onChange={(e) => {
                                    if (e.target.value) {
                                      setYekdemPrice(parseFloat(e.target.value));
                                      setPriceModified(true); setPriceSaved(false);
                                    }
                                  }}
                                >
                                  <option value="">2026 Öngörü</option>
                                  {yekdemPresets.map(p => (
                                    <option key={p.value} value={p.value.toString()}>{p.label}</option>
                                  ))}
                                </select>
                                <div className="pointer-events-none text-gray-400">
                                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                                </div>
                              </div>
                            )}
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </div>
                
                {/* PTF/YEKDEM Kaydet Butonu — değer değiştirildiğinde görünür */}
                {priceModified && (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={priceSaving}
                      onClick={async () => {
                        const period = manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '');
                        if (!period) return;
                        setPriceSaving(true);
                        try {
                          await fetch(`${API_BASE}/api/epias/prices/${period}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                              ptf_tl_per_mwh: ptfPrice,
                              yekdem_tl_per_mwh: yekdemPrice,
                            }),
                          });
                          setPriceModified(false);
                          setPriceSaved(true);
                          setTimeout(() => setPriceSaved(false), 3000);
                        } catch (err) {
                          console.error('Fiyat kaydetme hatası:', err);
                        } finally {
                          setPriceSaving(false);
                        }
                      }}
                      className="flex-1 px-2 py-1 text-xs font-medium bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors disabled:opacity-50"
                    >
                      {priceSaving ? '⏳ Kaydediliyor...' : '💾 PTF/YEKDEM Kaydet'}
                    </button>
                    <button
                      type="button"
                      onClick={() => { setPriceModified(false); }}
                      className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700"
                    >
                      İptal
                    </button>
                  </div>
                )}
                {priceSaved && (
                  <div className="text-xs text-green-600 font-medium">✅ Fiyatlar kaydedildi</div>
                )}
                
                {/* Çarpan */}
                <div>
                  <label className="text-xs font-medium text-gray-700 mb-1 block">Çarpan (Kar Marjı)</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      className="w-20 px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none"
                      value={multiplier || ''}
                      onChange={(e) => setMultiplier(e.target.value === '' ? 0 : parseFloat(e.target.value))}
                      onFocus={(e) => { if (multiplier === 0) e.target.value = ''; }}
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
                
                {/* Bayi Komisyonu — Segment Bazlı (Ek Şartname) */}
                <div className="pt-2 border-t border-gray-100">
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-medium text-gray-700">Bayi Komisyonu</label>
                    <button
                      type="button"
                      onClick={() => setBayiEnabled(!bayiEnabled)}
                      className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${
                        bayiEnabled ? 'bg-orange-500' : 'bg-gray-300'
                      }`}
                    >
                      <span
                        className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                          bayiEnabled ? 'translate-x-4' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>
                  {bayiEnabled && (
                    <div className="space-y-1.5">
                      {/* Segment bilgisi */}
                      {liveCalculation?.bayi_segment && (
                        <div className={`text-xs px-2 py-1 rounded font-medium ${
                          liveCalculation.bayi_requires_approval 
                            ? 'bg-red-50 text-red-700 border border-red-200' 
                            : 'bg-orange-50 text-orange-700'
                        }`}>
                          📋 {liveCalculation.bayi_segment.name} Segment — {liveCalculation.bayi_points}p bayi / {liveCalculation.total_margin_points.toFixed(1)}p toplam
                          {liveCalculation.bayi_requires_approval && ' ⚠️ Gelka onayı gerekli'}
                        </div>
                      )}
                      {multiplier < 1.01 && (
                        <div className="text-xs text-gray-500 bg-gray-50 p-1.5 rounded">
                          Katsayı 1.01 altında — bayi komisyonu uygulanmaz
                        </div>
                      )}

                      {/* Özel onay segmenti için manuel puan girişi */}
                      {liveCalculation?.bayi_requires_approval && (
                        <div className="flex items-center gap-2">
                          <label className="text-xs text-red-600">Özel Puan:</label>
                          <input
                            type="number"
                            className="w-20 px-2 py-1 text-sm border border-red-200 rounded focus:ring-1 focus:ring-red-500 bg-red-50"
                            value={bayiOzelOnayPuan || ''}
                            onChange={(e) => setBayiOzelOnayPuan(e.target.value === '' ? 0 : parseFloat(e.target.value))}
                            step="0.1"
                            min="0"
                            max="5"
                          />
                          <span className="text-xs text-gray-500">puan</span>
                        </div>
                      )}

                      {/* Hesaplama sonuçları */}
                      {liveCalculation && (
                        <div className="text-xs space-y-0.5 bg-orange-50 p-1.5 rounded">
                          <div className="flex justify-between">
                            <span className="text-gray-600">Toplam Marj:</span>
                            <span className="font-medium">{liveCalculation.total_margin_points.toFixed(1)}p = {formatCurrency(liveCalculation.supplier_profit_tl)}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-orange-600">Bayi ({liveCalculation.bayi_points}p):</span>
                            <span className="font-medium text-orange-700">{formatCurrency(liveCalculation.bayi_commission_tl)}</span>
                          </div>
                          <div className="flex justify-between border-t border-orange-200 pt-0.5">
                            <span className="text-green-600 font-medium">Gelka Net ({liveCalculation.gelka_points.toFixed(1)}p):</span>
                            <span className="font-bold text-green-700">{formatCurrency(liveCalculation.gelka_net_profit_tl)}</span>
                          </div>
                          <button
                            onClick={() => {
                              if (!liveCalculation) return;
                              const ptfKwh = ptfPrice / 1000;
                              const yekdemKwh = yekdemPrice / 1000;
                              const kwh = manualMode ? manualValues.consumption_kwh : (result?.extraction?.consumption_kwh?.value || 0);
                              const offerBasePrice = liveCalculation.include_yekdem ? (ptfKwh + yekdemKwh) : ptfKwh;
                              generateBayiRaporPdf({
                                customerName: customerInfo.company_name || 'Belirtilmedi',
                                contactPerson: customerInfo.contact_person,
                                invoicePeriod: manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '-'),
                                raporTarihi: new Date().toLocaleDateString('tr-TR'),
                                consumptionKwh: kwh,
                                ptfTlPerMwh: ptfPrice,
                                yekdemTlPerMwh: liveCalculation.include_yekdem ? yekdemPrice : 0,
                                multiplier,
                                bayiPoints: liveCalculation.bayi_points,
                                gelkaPoints: liveCalculation.gelka_points,
                                totalMarginPoints: liveCalculation.total_margin_points,
                                segmentName: liveCalculation.bayi_segment?.name || 'Komisyon Yok',
                                offerEnergyBase: kwh * offerBasePrice,
                                supplierProfitTl: liveCalculation.supplier_profit_tl,
                                bayiCommissionTl: liveCalculation.bayi_commission_tl,
                                gelkaNetProfitTl: liveCalculation.gelka_net_profit_tl,
                                offerTotalWithVatTl: liveCalculation.offer_total_with_vat_tl,
                                currentTotalWithVatTl: liveCalculation.current_total_with_vat_tl,
                                savingsRatio: liveCalculation.savings_ratio,
                              });
                            }}
                            className="w-full mt-1 px-2 py-1 text-xs bg-orange-100 text-orange-700 rounded hover:bg-orange-200 transition-colors"
                          >
                            📄 Bayi Raporu İndir
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                
                {/* ═══ Risk Analizi Paneli ═══ */}
                <div className="pt-2 border-t border-gray-100">
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-medium text-gray-700">📊 Risk Analizi</label>
                    <button
                      type="button"
                      onClick={() => setRiskPanelEnabled(!riskPanelEnabled)}
                      className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${
                        riskPanelEnabled ? 'bg-blue-500' : 'bg-gray-300'
                      }`}
                    >
                      <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                        riskPanelEnabled ? 'translate-x-4' : 'translate-x-0.5'
                      }`} />
                    </button>
                  </div>
                  {riskPanelEnabled && (
                    <div className="space-y-2">
                      {/* Giriş Modu Seçici */}
                      <div className="flex rounded-lg overflow-hidden border border-blue-200">
                        <button
                          type="button"
                          onClick={() => { setInputMode('template'); setRiskResult(null); }}
                          className={`flex-1 px-2 py-1 text-xs font-medium transition-colors ${
                            inputMode === 'template'
                              ? 'bg-blue-600 text-white'
                              : 'bg-blue-50 text-blue-700 hover:bg-blue-100'
                          }`}
                        >
                          Şablon Profili
                        </button>
                        <button
                          type="button"
                          onClick={() => { setInputMode('t1t2t3'); setRiskResult(null); }}
                          className={`flex-1 px-2 py-1 text-xs font-medium transition-colors ${
                            inputMode === 't1t2t3'
                              ? 'bg-blue-600 text-white'
                              : 'bg-blue-50 text-blue-700 hover:bg-blue-100'
                          }`}
                        >
                          Gerçek T1/T2/T3
                        </button>
                      </div>

                      {/* Şablon Modu: Mevcut şablon seçimi */}
                      {inputMode === 'template' && (
                        <div className="flex items-center gap-2">
                          <select
                            className="flex-1 px-2 py-1 text-xs border border-blue-200 rounded bg-blue-50 focus:ring-1 focus:ring-blue-500"
                            value={riskTemplateName}
                            onChange={(e) => setRiskTemplateName(e.target.value)}
                          >
                          {riskTemplates.length > 0 ? (
                            riskTemplates.map(t => (
                              <option key={t.name} value={t.name}>{t.display_name}</option>
                            ))
                          ) : (
                            <>
                              <option value="3_vardiya_sanayi">3 Vardiya Sanayi</option>
                              <option value="tek_vardiya_fabrika">Tek Vardiya Fabrika</option>
                              <option value="ofis">Ofis</option>
                              <option value="otel">Otel</option>
                              <option value="restoran">Restoran</option>
                              <option value="soguk_hava_deposu">Soğuk Hava Deposu</option>
                              <option value="gece_agirlikli_uretim">Gece Ağırlıklı Üretim</option>
                              <option value="avm">AVM</option>
                              <option value="akaryakit_istasyonu">Akaryakıt İstasyonu</option>
                              <option value="market_supermarket">Market / Süpermarket</option>
                              <option value="hastane">Hastane</option>
                              <option value="tarimsal_sulama">Tarımsal Sulama</option>
                              <option value="site_yonetimi">Site Yönetimi</option>
                            </>
                          )}
                        </select>
                          <button
                            type="button"
                            onClick={runRiskAnalysis}
                            disabled={riskLoading}
                            className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 transition-colors"
                          >
                            {riskLoading ? '⏳ Analiz ediliyor...' : '🔍 Analiz'}
                          </button>
                        </div>
                      )}

                      {/* T1/T2/T3 Modu: kWh giriş alanları */}
                      {inputMode === 't1t2t3' && (
                        <div className="space-y-1.5">
                          <div className="grid grid-cols-3 gap-1.5">
                            <div>
                              <label className="text-[10px] text-gray-500 block mb-0.5">Gündüz / T1 (kWh)</label>
                              <input
                                type="number"
                                className="w-full px-2 py-1 text-xs border border-blue-200 rounded bg-blue-50 focus:ring-1 focus:ring-blue-500"
                                value={t1Kwh || ''}
                                onChange={(e) => setT1Kwh(e.target.value === '' ? 0 : parseFloat(e.target.value) || 0)}
                                onFocus={(e) => { if (t1Kwh === 0) e.target.value = ''; }}
                                min="0"
                                step="0.01"
                                placeholder="0"
                              />
                            </div>
                            <div>
                              <label className="text-[10px] text-gray-500 block mb-0.5">Puant / T2 (kWh)</label>
                              <input
                                type="number"
                                className="w-full px-2 py-1 text-xs border border-blue-200 rounded bg-blue-50 focus:ring-1 focus:ring-blue-500"
                                value={t2Kwh || ''}
                                onChange={(e) => setT2Kwh(e.target.value === '' ? 0 : parseFloat(e.target.value) || 0)}
                                onFocus={(e) => { if (t2Kwh === 0) e.target.value = ''; }}
                                min="0"
                                step="0.01"
                                placeholder="0"
                              />
                            </div>
                            <div>
                              <label className="text-[10px] text-gray-500 block mb-0.5">Gece / T3 (kWh)</label>
                              <input
                                type="number"
                                className="w-full px-2 py-1 text-xs border border-blue-200 rounded bg-blue-50 focus:ring-1 focus:ring-blue-500"
                                value={t3Kwh || ''}
                                onChange={(e) => setT3Kwh(e.target.value === '' ? 0 : parseFloat(e.target.value) || 0)}
                                onFocus={(e) => { if (t3Kwh === 0) e.target.value = ''; }}
                                min="0"
                                step="0.01"
                                placeholder="0"
                              />
                            </div>
                          </div>
                          {/* Toplam + Gerilim Seviyesi */}
                          <div className="flex items-center justify-between">
                            <span className="text-xs text-gray-600">
                              Toplam: <span className="font-bold text-blue-700">{totalT1T2T3.toLocaleString('tr-TR', {minimumFractionDigits: 1})} kWh</span>
                            </span>
                            <div className="flex items-center gap-1">
                              <span className="text-[10px] text-gray-500">Gerilim:</span>
                              <select
                                className="px-1.5 py-0.5 text-xs border border-blue-200 rounded bg-blue-50 focus:ring-1 focus:ring-blue-500"
                                value={voltageLevel}
                                onChange={(e) => setVoltageLevel(e.target.value as 'ag' | 'og')}
                              >
                                <option value="og">OG (Orta Gerilim)</option>
                                <option value="ag">AG (Alçak Gerilim)</option>
                              </select>
                            </div>
                          </div>
                          {/* Uyarı: tümü sıfır */}
                          {allT1T2T3Zero && (
                            <div className="text-xs text-amber-600 bg-amber-50 p-1 rounded">
                              ⚠ En az bir zaman diliminde tüketim giriniz
                            </div>
                          )}
                          {/* Analiz butonu */}
                          <button
                            type="button"
                            onClick={runRiskAnalysis}
                            disabled={riskLoading || allT1T2T3Zero}
                            className="w-full px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 transition-colors"
                          >
                            {riskLoading ? '⏳ Analiz ediliyor...' : '🔍 Analiz Et'}
                          </button>
                        </div>
                      )}

                      {/* Hata mesajı */}
                      {riskError && (
                        <div className="text-xs text-red-600 bg-red-50 p-1.5 rounded">
                          ⚠ {riskError}
                        </div>
                      )}

                      {/* Loading */}
                      {riskLoading && !riskResult && (
                        <div className="text-xs text-blue-600 bg-blue-50 p-2 rounded text-center">
                          ⏳ Risk analizi hesaplanıyor...
                        </div>
                      )}

                      {/* Sonuçlar */}
                      {riskResult && (
                        <div className="space-y-1.5 bg-blue-50 p-2 rounded text-xs">
                          {/* T1/T2/T3 Dağılım Gösterimi */}
                          {riskResult.time_zone_breakdown && (
                            <div className="bg-white/60 p-1.5 rounded">
                              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                                {(['T1', 'T2', 'T3'] as const).map((zone) => {
                                  const tz = riskResult.time_zone_breakdown?.[zone];
                                  if (!tz) return null;
                                  return (
                                    <span key={zone} className="text-gray-700">
                                      <span className="font-medium">{zone}:</span>{' '}
                                      {tz.consumption_kwh.toLocaleString('tr-TR', {maximumFractionDigits: 0})} kWh
                                      {' '}
                                      <span className="text-gray-500">(%{tz.consumption_pct.toFixed(1)})</span>
                                    </span>
                                  );
                                })}
                              </div>
                              {/* Puant risk uyarıları */}
                              {(() => {
                                const t2pct = riskResult.time_zone_breakdown?.['T2']?.consumption_pct || 0;
                                if (t2pct >= 55) return (
                                  <div className="mt-1 text-xs text-red-700 bg-red-50 p-1 rounded font-medium">
                                    🔴 Kritik puant yoğunlaşması — fiyatlama riski yüksek
                                  </div>
                                );
                                if (t2pct >= 40) return (
                                  <div className="mt-1 text-xs text-amber-700 bg-amber-50 p-1 rounded">
                                    ⚠️ Puant tüketim oranı yüksek — enerji maliyeti artabilir
                                  </div>
                                );
                                return null;
                              })()}
                            </div>
                          )}

                          {/* Dağıtım Bedeli */}
                          {riskResult.distribution && (
                            <div className="flex justify-between items-center">
                              <span className="text-gray-600">Dağıtım Bedeli ({riskResult.distribution.voltage_level}):</span>
                              <span className="font-medium text-gray-800">
                                {riskResult.distribution.unit_price_tl_per_kwh.toFixed(2)} TL/kWh × {riskResult.distribution.total_kwh.toLocaleString('tr-TR', {maximumFractionDigits: 0})} kWh = {riskResult.distribution.total_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL
                              </span>
                            </div>
                          )}

                          {/* Risk Flags — LOSS_RISK (P1) ve UNPROFITABLE_OFFER (P2) */}
                          {riskResult.pricing.risk_flags && riskResult.pricing.risk_flags.length > 0 && (() => {
                            const hasLossRisk = riskResult.pricing.risk_flags.some((f: any) => f.type === 'LOSS_RISK');
                            const hasUnprofitable = riskResult.pricing.risk_flags.some((f: any) => f.type === 'UNPROFITABLE_OFFER');
                            return (
                              <div className={`p-2 rounded text-xs font-medium ${
                                hasLossRisk ? 'bg-red-100 text-red-800 border border-red-300' : 'bg-yellow-100 text-yellow-800 border border-yellow-300'
                              }`}>
                                {hasLossRisk && <div>⛔ ZARAR RİSKİ — Net marj negatif, teklif zarar üretir</div>}
                                {hasUnprofitable && <div>⚠️ KÂRSIZ TEKLİF — Dağıtım dahil brüt marj negatif</div>}
                              </div>
                            );
                          })()}

                          {/* Dual Margin — Enerji Marjı / Toplam Etki / Net Marj */}
                          {riskResult.pricing.gross_margin_energy_per_mwh !== undefined && (
                            <div className="bg-white/60 p-1.5 rounded space-y-0.5">
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Enerji Marjı:</span>
                                <span className={`font-medium ${(riskResult.pricing.gross_margin_energy_per_mwh || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {(riskResult.pricing.gross_margin_energy_per_mwh || 0).toFixed(2)} TL/MWh
                                </span>
                              </div>
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Toplam Etki:</span>
                                <span className={`font-medium ${(riskResult.pricing.gross_margin_total_per_mwh || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {(riskResult.pricing.gross_margin_total_per_mwh || 0).toFixed(2)} TL/MWh
                                </span>
                              </div>
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Net Marj:</span>
                                <span className={`font-bold ${(riskResult.pricing.net_margin_per_mwh || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {(riskResult.pricing.net_margin_per_mwh || 0).toFixed(2)} TL/MWh
                                </span>
                              </div>
                            </div>
                          )}

                          {/* Brüt Marj (toplam TL — backward compat) */}
                          <div className="flex justify-between items-center">
                            <span className="text-gray-600">Brüt Marj (TL):</span>
                            <span className={`font-bold ${riskResult.pricing.total_gross_margin_tl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                              {riskResult.pricing.total_gross_margin_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL
                              {riskResult.pricing.total_gross_margin_tl < 0 && <span className="ml-1 text-red-700 font-bold">Zarar</span>}
                            </span>
                          </div>

                          {/* Risk Buffer Bilgi Kartı — yalnızca riskResult + şablon seçili */}
                          {selectedTemplate && (
                            <div className="bg-white/60 p-1.5 rounded space-y-1">
                              <div className="text-xs font-medium text-gray-700">
                                📊 Baz Marj: %{baseMarginPct.toFixed(1)} | Risk Tamponu: %{riskBufferPct.toFixed(1)} | Önerilen: %{recommendedMarginPct.toFixed(1)}
                              </div>
                              {riskBufferPct === 0 && (
                                <div className="text-xs text-gray-500">Tampon: %0 (düşük riskli profil)</div>
                              )}
                              {baseMarginPct < recommendedMarginPct && (
                                <div className="text-xs text-amber-700 bg-amber-50 p-1 rounded font-medium">
                                  ⚠️ Seçilen katsayı önerilen marjın altında — risk tamponu karşılanmıyor
                                </div>
                              )}
                            </div>
                          )}

                          {/* Template Profil Bilgisi — şablon seçildiğinde T1/T2/T3 + risk seviyesi */}
                          {selectedTemplate && (
                            <div className="bg-white/60 p-1.5 rounded space-y-0.5">
                              <div className="text-xs text-gray-700">
                                📋 T1: %{selectedTemplate.t1_pct} | T2: %{selectedTemplate.t2_pct} | T3: %{selectedTemplate.t3_pct}
                              </div>
                              <div className="text-xs text-gray-700">
                                Risk Seviyesi: <span className="font-medium">{RISK_LEVEL_LABELS[selectedTemplate.risk_level] ?? selectedTemplate.risk_level}</span>
                              </div>
                            </div>
                          )}

                          {/* Marj Gerçekliği Karar Kartı */}
                          {riskResult.margin_reality ? (() => {
                            const mr = riskResult.margin_reality;
                            const decisionColors: Record<string, string> = {
                              'TEKLİF UYGUN': 'bg-green-50 text-green-700 border-green-200',
                              'FİYAT ARTIR': 'bg-amber-50 text-amber-700 border-amber-200',
                              'TEKLİF VERME': 'bg-red-50 text-red-700 border-red-200',
                              'FİYAT DÜŞÜR': 'bg-blue-50 text-blue-700 border-blue-200',
                            };
                            const decisionBadgeColors: Record<string, string> = {
                              'TEKLİF UYGUN': 'bg-green-600',
                              'FİYAT ARTIR': 'bg-amber-500',
                              'TEKLİF VERME': 'bg-red-600',
                              'FİYAT DÜŞÜR': 'bg-blue-600',
                            };
                            const aggressColors: Record<string, string> = {
                              'YOK': 'bg-gray-100 text-gray-600',
                              'DÜŞÜK': 'bg-green-100 text-green-700',
                              'ORTA': 'bg-amber-100 text-amber-700',
                              'YÜKSEK': 'bg-red-100 text-red-700',
                            };
                            return (
                              <>
                                {/* Ana Karar Kartı */}
                                <div className={`p-2.5 rounded-lg border ${decisionColors[mr.pricing_decision] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>
                                  <div className="flex items-center justify-between mb-1.5">
                                    <span className={`px-2 py-0.5 rounded text-xs font-bold text-white ${decisionBadgeColors[mr.pricing_decision] || 'bg-gray-500'}`}>
                                      {mr.pricing_decision}
                                    </span>
                                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${aggressColors[mr.pricing_aggressiveness] || 'bg-gray-100 text-gray-600'}`}>
                                      Agresiflik: {mr.pricing_aggressiveness}
                                    </span>
                                  </div>
                                  {/* Özet satır */}
                                  <div className="text-xs font-medium mb-2">
                                    %{mr.nominal_margin_pct.toFixed(1)} sattın → gerçekte %{mr.real_margin_pct.toFixed(1)} kazandın → ×{mr.required_multiplier_for_target.toFixed(3)} ile fiyatla
                                  </div>
                                  {mr.pricing_decision_reason && (
                                    <div className="text-[10px] opacity-80 mb-2">{mr.pricing_decision_reason}</div>
                                  )}
                                  {/* Manuel vs Gerçek Marj */}
                                  <div className="flex items-center gap-2 text-xs mb-1.5">
                                    <div className="flex-1 text-center p-1 bg-white/50 rounded">
                                      <div className="text-[10px] text-gray-500">Manuel Marj</div>
                                      <div className="font-bold">%{mr.nominal_margin_pct.toFixed(1)}</div>
                                    </div>
                                    <span className="text-lg">→</span>
                                    <div className="flex-1 text-center p-1 bg-white/50 rounded">
                                      <div className="text-[10px] text-gray-500">Gerçek Marj</div>
                                      <div className={`font-bold ${mr.real_margin_pct >= 0 ? 'text-green-700' : 'text-red-700'}`}>%{mr.real_margin_pct.toFixed(1)}</div>
                                    </div>
                                  </div>
                                  {/* Marj Sapması */}
                                  <div className="flex justify-between items-center text-xs mb-1">
                                    <span>Marj Sapması:</span>
                                    <span className={`font-bold ${mr.margin_deviation_pct <= 0 ? 'text-red-600' : 'text-green-600'}`}>
                                      %{mr.margin_deviation_pct.toFixed(1)} ({mr.margin_deviation_tl.toLocaleString('tr-TR', {minimumFractionDigits: 0, maximumFractionDigits: 0})} TL)
                                    </span>
                                  </div>
                                  {/* Tahmini Kâr */}
                                  <div className="flex justify-between items-center text-xs mb-1">
                                    <span>Tahmini Kâr:</span>
                                    <span className={`font-bold ${mr.real_margin_tl >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                                      {mr.real_margin_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL
                                    </span>
                                  </div>
                                  {/* Önerilen Katsayı + Uygula */}
                                  <div className="flex justify-between items-center text-xs mt-2 pt-1.5 border-t border-current/10">
                                    <span>Önerilen Katsayı:</span>
                                    <div className="flex items-center gap-1.5">
                                      <span className="font-bold text-sm">×{mr.required_multiplier_for_target.toFixed(3)}</span>
                                      <button
                                        type="button"
                                        onClick={() => setMultiplier(riskResult!.margin_reality!.required_multiplier_for_target)}
                                        className="px-2 py-0.5 text-[10px] font-medium bg-white/80 border border-current/20 rounded hover:bg-white transition-colors"
                                      >
                                        Uygula
                                      </button>
                                    </div>
                                  </div>
                                </div>

                                {/* Detay Bölümü (açılır/kapanır) */}
                                <div className="bg-white/60 rounded">
                                  <button
                                    type="button"
                                    onClick={() => setMarginDetailOpen(!marginDetailOpen)}
                                    className="w-full flex items-center justify-between px-2 py-1 text-xs text-gray-600 hover:text-gray-800 transition-colors"
                                  >
                                    <span className="font-medium">📊 Marj Detayları</span>
                                    <span className="text-[10px]">{marginDetailOpen ? '▲ Kapat' : '▼ Aç'}</span>
                                  </button>
                                  {marginDetailOpen && (
                                    <div className="px-2 pb-2 space-y-1 text-xs">
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Efektif Katsayı:</span>
                                        <span className="font-medium">×{mr.effective_multiplier.toFixed(3)} <span className="text-gray-400">(Δ {mr.multiplier_delta >= 0 ? '+' : ''}{mr.multiplier_delta.toFixed(3)})</span></span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Başabaş Katsayı:</span>
                                        <span className="font-medium">×{mr.break_even_multiplier.toFixed(3)}</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Güvenli Katsayı:</span>
                                        <span className="font-medium text-blue-700">×{mr.safe_multiplier.toFixed(3)}</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Negatif Marj Saatleri:</span>
                                        <span className="font-medium text-red-600">{mr.negative_margin_hours} / {mr.total_hours} saat</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Pozitif Marj Toplamı:</span>
                                        <span className="font-medium text-green-600">{mr.positive_margin_total_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-600">Negatif Marj Toplamı:</span>
                                        <span className="font-medium text-red-600">{mr.negative_margin_total_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL</span>
                                      </div>
                                    </div>
                                  )}
                                </div>

                                {/* Eski risk bilgileri — küçük yardımcı */}
                                <div className="bg-white/40 p-1.5 rounded space-y-0.5 text-[10px] text-gray-500">
                                  <div className="flex justify-between">
                                    <span>Risk Seviyesi:</span>
                                    <span className={`font-medium px-1 py-0.5 rounded text-white text-[9px] ${
                                      riskResult.risk_score.score === 'Düşük' ? 'bg-green-500' :
                                      riskResult.risk_score.score === 'Orta' ? 'bg-yellow-500' : 'bg-red-500'
                                    }`}>{riskResult.risk_score.score}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Zarar Saati:</span>
                                    <span className="font-medium text-red-500">{riskResult.loss_map.total_loss_hours} saat</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Sapma:</span>
                                    <span className="font-medium">%{riskResult.risk_score.deviation_pct.toFixed(1)}</span>
                                  </div>
                                </div>
                              </>
                            );
                          })() : (
                            <>
                              {/* Fallback: eski güvenli katsayı + risk gösterimi (margin_reality yoksa) */}
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Güvenli Katsayı:</span>
                                <span className="font-bold text-blue-700">×{riskResult.safe_multiplier.safe_multiplier.toFixed(3)}</span>
                              </div>
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Önerilen:</span>
                                <span className="font-medium text-blue-600">×{riskResult.safe_multiplier.recommended_multiplier.toFixed(2)}</span>
                              </div>
                              <div className="flex justify-between items-center">
                                <span className="text-gray-600">Risk Seviyesi:</span>
                                <span className={`font-bold px-1.5 py-0.5 rounded text-white ${
                                  riskResult.risk_score.score === 'Düşük' ? 'bg-green-500' :
                                  riskResult.risk_score.score === 'Orta' ? 'bg-yellow-500' : 'bg-red-500'
                                }`}>{riskResult.risk_score.score}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-gray-600">Zarar Saati:</span>
                                <span className="font-medium text-red-600">{riskResult.loss_map.total_loss_hours} saat</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-gray-600">Net Marj:</span>
                                <span className={`font-medium ${riskResult.pricing.total_net_margin_tl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {riskResult.pricing.total_net_margin_tl.toLocaleString('tr-TR', {minimumFractionDigits: 2})} TL
                                </span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-gray-600">Sapma:</span>
                                <span className="font-medium">%{riskResult.risk_score.deviation_pct.toFixed(1)}</span>
                              </div>
                            </>
                          )}

                          {/* Uyarılar */}
                          {riskResult.warnings.filter(w => w.message).map((w, i) => (
                            <div key={i} className="text-xs text-amber-700 bg-amber-50 p-1 rounded">
                              ⚠ {w.message}
                            </div>
                          ))}

                          {/* Seçilen katsayı uyarısı */}
                          {multiplier < riskResult.safe_multiplier.safe_multiplier && (
                            <div className="text-xs text-red-700 bg-red-100 p-1.5 rounded font-medium">
                              ⛔ Seçilen ×{multiplier.toFixed(2)} güvenli katsayının (×{riskResult.safe_multiplier.safe_multiplier.toFixed(3)}) altında — zarar riski!
                              <button
                                type="button"
                                onClick={() => setMultiplier(riskResult!.safe_multiplier.recommended_multiplier)}
                                className="ml-2 px-2 py-0.5 text-xs bg-red-600 text-white rounded hover:bg-red-700 transition-colors"
                              >
                                ×{riskResult.safe_multiplier.recommended_multiplier.toFixed(2)} uygula
                              </button>
                            </div>
                          )}

                          {/* Rapor indirme butonları */}
                          <div className="flex gap-1 pt-1">
                            <button
                              type="button"
                              onClick={async () => {
                                try {
                                  const period = manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '');
                                  const np = normalizeInvoicePeriod(period || '') || '';
                                  const kwh = manualMode ? manualValues.consumption_kwh : (result?.extraction?.consumption_kwh?.value || 0);
                                  const reqParams: any = {
                                    period: np, multiplier, dealer_commission_pct: bayiEnabled ? (getBayiSegment(multiplier)?.bayiPoints || 0) : 0,
                                  };
                                  if (inputMode === 't1t2t3') {
                                    reqParams.use_template = false;
                                    reqParams.t1_kwh = t1Kwh;
                                    reqParams.t2_kwh = t2Kwh;
                                    reqParams.t3_kwh = t3Kwh;
                                    reqParams.template_monthly_kwh = totalT1T2T3;
                                    reqParams.voltage_level = voltageLevel;
                                  } else {
                                    reqParams.use_template = true;
                                    reqParams.template_name = riskTemplateName;
                                    reqParams.template_monthly_kwh = kwh;
                                  }
                                  const blob = await pricingDownloadPdf(reqParams, 'internal');
                                  const url = URL.createObjectURL(blob);
                                  const a = document.createElement('a'); a.href = url;
                                  a.download = `risk_analiz_${np}.pdf`; a.click();
                                  URL.revokeObjectURL(url);
                                } catch (e: any) { setRiskError(e.message); }
                              }}
                              className="flex-1 px-2 py-1 text-xs bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors"
                            >
                              📄 PDF Rapor
                            </button>
                            <button
                              type="button"
                              onClick={async () => {
                                try {
                                  const period = manualMode ? manualValues.invoice_period : (result?.extraction?.invoice_period || '');
                                  const np = normalizeInvoicePeriod(period || '') || '';
                                  const kwh = manualMode ? manualValues.consumption_kwh : (result?.extraction?.consumption_kwh?.value || 0);
                                  const reqParams: any = {
                                    period: np, multiplier, dealer_commission_pct: bayiEnabled ? (getBayiSegment(multiplier)?.bayiPoints || 0) : 0,
                                  };
                                  if (inputMode === 't1t2t3') {
                                    reqParams.use_template = false;
                                    reqParams.t1_kwh = t1Kwh;
                                    reqParams.t2_kwh = t2Kwh;
                                    reqParams.t3_kwh = t3Kwh;
                                    reqParams.template_monthly_kwh = totalT1T2T3;
                                    reqParams.voltage_level = voltageLevel;
                                  } else {
                                    reqParams.use_template = true;
                                    reqParams.template_name = riskTemplateName;
                                    reqParams.template_monthly_kwh = kwh;
                                  }
                                  const blob = await pricingDownloadExcel(reqParams);
                                  const url = URL.createObjectURL(blob);
                                  const a = document.createElement('a'); a.href = url;
                                  a.download = `risk_analiz_${np}.xlsx`; a.click();
                                  URL.revokeObjectURL(url);
                                } catch (e: any) { setRiskError(e.message); }
                              }}
                              className="flex-1 px-2 py-1 text-xs bg-green-100 text-green-700 rounded hover:bg-green-200 transition-colors"
                            >
                              📊 Excel Rapor
                            </button>
                          </div>

                          {riskResult.cache_hit && (
                            <div className="text-[10px] text-blue-400 text-right">⚡ Cache</div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
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
                      const tariff = activeTariffs.find(t => t.key === key);
                      if (tariff) {
                        setManualValues(prev => ({...prev, tariff_group: tariff.label}));
                        
                        // BTV Oranları (2464 sayılı Kanun Madde 34):
                        // OSB: Belediye sınırları dışındaki OSB'ler BTV muaf (%0)
                        // Belediye sınırları içindeki OSB'ler (Çerkezköy gibi) normal oran
                        // Sanayi (imal/istihsal kapsamı): %1
                        // Diğer tüm gruplar: %5
                        if (tariff.key === 'osb_ikitelli') {
                          setBtvRate(0);  // Belediye sınırları dışı OSB
                        } else if (tariff.key === 'osb_cerkezkoy') {
                          setBtvRate(0.05);  // Çerkezköy OSB — belediye sınırları içi, %5 BTV
                        } else if (tariff.group === 'sanayi') {
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
                    <option value="custom">✏️ Manuel Giriş</option>
                    {(() => {
                      const tariffs = activeTariffs.filter(t => t.key !== 'custom');
                      if (tariffs.length === 0) return null;
                      
                      // Grup bazlı ayır
                      const groupLabels: Record<string, string> = {
                        sanayi: '⚡ Sanayi',
                        ticarethane: '🏪 Ticarethane',
                        mesken: '🏠 Mesken',
                        tarimsal: '🌾 Tarımsal',
                        aydinlatma: '💡 Aydınlatma',
                        kamu_ozel: '🏛️ Kamu/Özel',
                        osb_cerkezkoy: '🏭 Çerkezköy OSB',
                        osb_ikitelli: '🏭 İkitelli OSB',
                      };
                      
                      // Grupları belirle
                      const groups: Record<string, typeof tariffs> = {};
                      tariffs.forEach(t => {
                        const g = t.group || 'diger';
                        if (!groups[g]) groups[g] = [];
                        groups[g].push(t);
                      });
                      
                      // Sıralama: sanayi, osb'ler, ticarethane, mesken, tarimsal, aydinlatma, kamu
                      const order = ['sanayi', 'osb_cerkezkoy', 'osb_ikitelli', 'ticarethane', 'mesken', 'tarimsal', 'aydinlatma', 'kamu_ozel'];
                      const sortedGroups = Object.keys(groups).sort((a, b) => {
                        const ia = order.indexOf(a);
                        const ib = order.indexOf(b);
                        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
                      });
                      
                      return sortedGroups.map(g => (
                        <optgroup key={g} label={groupLabels[g] || g}>
                          {groups[g].map(tariff => (
                            <option key={tariff.key} value={tariff.key}>
                              {tariff.label} — {(tariff.price * 1000).toFixed(2)} kr/kWh
                            </option>
                          ))}
                        </optgroup>
                      ));
                    })()}
                  </select>
                  
                  {distributionTariffKey === 'custom' && (
                    <input
                      type="number"
                      className="w-full mt-1 px-2 py-1.5 text-sm border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 focus:border-primary-500 outline-none"
                      placeholder="Birim fiyat TL/kWh (örn: 1.07050)"
                      value={customDistributionPrice || ''}
                      onChange={(e) => setCustomDistributionPrice(e.target.value === '' ? 0 : parseFloat(e.target.value) || 0)}
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
                  {tariffWarning && (
                    <div className="text-xs text-amber-700 bg-amber-50 p-1.5 rounded mt-1">
                      ⚠ {tariffWarning}
                      <button
                        type="button"
                        className="ml-2 underline font-medium hover:text-amber-900"
                        onClick={() => setDistributionTariffKey('custom')}
                      >
                        Manuel giriş yap →
                      </button>
                    </div>
                  )}
                </div>
                
                {/* BTV Oranı */}
                <div className="pt-2 border-t border-gray-100">
                  <label className="text-xs font-medium text-gray-700 mb-1 block">BTV Oranı</label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setBtvRate(0)}
                      className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                        btvRate === 0
                          ? 'bg-primary-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      %0 (OSB)
                    </button>
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
          <div className="lg:col-span-2 space-y-2 overflow-auto">
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
                    <p className="text-xs text-purple-600 font-medium">
                      {bayiEnabled ? 'Gelka Net Kar' : 'Tedarikçi Karı'}
                    </p>
                    <p className="text-lg font-bold text-purple-900">
                      {formatCurrency(bayiEnabled ? liveCalculation.gelka_net_profit_tl : liveCalculation.supplier_profit_tl)}
                    </p>
                    <p className="text-xs text-purple-600">
                      %{liveCalculation.supplier_profit_margin.toFixed(1)} marj
                      {bayiEnabled && liveCalculation.bayi_segment && ` (${liveCalculation.bayi_segment.name} ${liveCalculation.bayi_points}p: ${formatCurrency(liveCalculation.bayi_commission_tl)})`}
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
                            tariff_group: (result.extraction as any)?.meta?.tariff_group_guess || 'Sanayi',
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
                      <div className="grid grid-cols-4 gap-2">
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
                        <div>
                          <label className="text-xs text-gray-500 block mb-1">Mevcut Birim Fiyat (TL/kWh)</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="w-full px-2 py-1 text-xs border border-blue-200 rounded focus:ring-1 focus:ring-blue-500 bg-blue-50"
                            value={currentUnitPriceInput}
                            onChange={(e) => {
                              setCurrentUnitPriceInput(e.target.value);
                              const parsed = parseNumber(e.target.value);
                              if (parsed > 0) {
                                setManualValues(prev => ({...prev, current_unit_price: parsed}));
                              }
                            }}
                            onBlur={(e) => {
                              const parsed = parseNumber(e.target.value);
                              setManualValues(prev => ({...prev, current_unit_price: parsed}));
                              if (parsed > 0) {
                                setCurrentUnitPriceInput(formatNumber(parsed));
                              }
                            }}
                            placeholder="2,85"
                            title="Mevcut tedarikçinin uyguladığı aktif enerji birim fiyatı"
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
                              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:ring-1 focus:ring-primary-500 bg-gray-50"
                              value={(() => {
                                const kwh = manualValues.consumption_kwh;
                                const unitPrice = manualValues.current_unit_price;
                                if (kwh > 0 && unitPrice > 0) {
                                  return formatNumber(unitPrice * kwh);
                                }
                                return manualValues.current_energy_tl ? formatNumber(manualValues.current_energy_tl) : '';
                              })()}
                              readOnly
                              title="Mevcut birim fiyat × kWh otomatik hesaplanır"
                            />
                          </div>
                          <div>
                            <label className="text-xs text-gray-500 block mb-1">Dağıtım Bedeli</label>
                            <input
                              type="number"
                              className={`w-full px-2 py-1 text-xs border rounded focus:ring-1 focus:ring-primary-500 ${
                                getDistributionUnitPrice() > 0 && manualValues.consumption_kwh > 0
                                  ? 'border-gray-200 bg-gray-50'
                                  : 'border-blue-300 bg-white'
                              }`}
                              value={(() => {
                                const distPrice = getDistributionUnitPrice();
                                const kwh = manualValues.consumption_kwh;
                                if (distPrice > 0 && kwh > 0) {
                                  return (distPrice * kwh).toFixed(2);
                                }
                                return manualValues.current_distribution_tl || '';
                              })()}
                              onChange={(e) => {
                                const val = e.target.value === '' ? 0 : parseFloat(e.target.value) || 0;
                                setManualValues(prev => ({...prev, current_distribution_tl: val}));
                              }}
                              readOnly={getDistributionUnitPrice() > 0 && manualValues.consumption_kwh > 0}
                              title={getDistributionUnitPrice() > 0 && manualValues.consumption_kwh > 0
                                ? "Dağıtım birim fiyatı × kWh otomatik hesaplanır"
                                : "Dağıtım bedeli TL olarak girin"}
                              step="0.01"
                              min="0"
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
                    result && (
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
                    )
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
                            {result?.calculation?.meta_distribution_source?.startsWith('epdk_tariff') ? (
                              <span className="text-primary-600 ml-1">EPDK</span>
                            ) : result?.calculation?.meta_distribution_source === 'extracted_from_invoice' ? (
                              <span className="text-gray-600 ml-1">Faturadan</span>
                            ) : (
                              <span className="text-amber-600 ml-1">Manuel</span>
                            )}
                          </span>
                          {result?.calculation?.meta_distribution_tariff_key && (
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
                      ) : result?.validation?.is_ready_for_pricing ? (
                        <>
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-green-700 font-medium">Analiz başarılı</span>
                        </>
                      ) : (
                        <>
                          <AlertCircle className="w-4 h-4 text-amber-500" />
                          <span className="text-amber-700">Eksik: {result?.validation?.missing_fields?.join(', ') ?? ''}</span>
                        </>
                      )}
                    </div>
                  </div>
                  {result?.calculation?.meta_distribution_mismatch_warning && (
                    <div className="mt-1 p-1 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
                      ⚠️ {result?.calculation?.meta_distribution_mismatch_warning}
                    </div>
                  )}
                  
                  {/* Hesap Detayı - Debug Panel */}
                  {result?.debug_meta && (
                    <details className="mt-2 pt-2 border-t border-gray-100">
                      <summary className="cursor-pointer text-xs font-medium text-gray-500 hover:text-gray-900 flex items-center gap-1">
                        <span>🔍 Hesap Detayı</span>
                        <span className="text-xs text-gray-400">(trace: {result?.debug_meta.trace_id || result?.meta?.trace_id})</span>
                        {result?.quality_score && (
                          <span className={`ml-1 px-1 py-0.5 rounded text-xs font-medium ${
                            result?.quality_score.grade === 'OK' ? 'bg-green-100 text-green-700' :
                            result?.quality_score.grade === 'CHECK' ? 'bg-yellow-100 text-yellow-700' :
                            'bg-red-100 text-red-700'
                          }`}>
                            {result?.quality_score.score} {result?.quality_score.grade}
                          </span>
                        )}
                      </summary>
                      <div className="mt-2 p-2 bg-gray-50 rounded text-xs space-y-1">
                        {result?.quality_score && result?.quality_score.flags.length > 0 && (
                          <div className="pb-1 border-b border-gray-200">
                            <span className="text-gray-600 font-medium">Bayraklar:</span>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {result?.quality_score.flag_details.map((flag, i) => (
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
                          <div><span className="text-gray-500">Dönem:</span> <span className="font-mono">{result?.debug_meta.pricing_period || result?.calculation?.meta_pricing_period || '-'}</span></div>
                          <div><span className="text-gray-500">Kaynak:</span> <span className={`font-medium ${result?.debug_meta.pricing_source === 'reference' ? 'text-primary-600' : 'text-amber-600'}`}>{result?.debug_meta.pricing_source || '-'}</span></div>
                          <div><span className="text-gray-500">PTF:</span> <span className="font-mono">{result?.debug_meta.ptf_tl_per_mwh || 0}</span></div>
                          <div><span className="text-gray-500">YEKDEM:</span> <span className="font-mono">{result?.debug_meta?.yekdem_tl_per_mwh || 0}</span></div>
                        </div>
                        
                        {result?.debug_meta?.warnings && result?.debug_meta?.warnings.length > 0 && (
                          <div className="pt-1 border-t border-gray-200 text-amber-600">
                            ⚠️ {result?.debug_meta?.warnings.join(', ')}
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
                  
                  {/* Teklif Birim Fiyat Gösterim Modu */}
                  <div className="flex items-center gap-1 mb-2">
                    <span className="text-[10px] text-gray-500 mr-1">Birim Fiyat:</span>
                    <button
                      type="button"
                      onClick={() => setOfferDisplayMode('energy')}
                      className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                        offerDisplayMode === 'energy' ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      Enerji (PTF+YEKDEM)
                    </button>
                    <button
                      type="button"
                      onClick={() => setOfferDisplayMode('combined')}
                      className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                        offerDisplayMode === 'combined' ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      Toplam (PTF+YEKDEM+Dağıtım)
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
                      {manualMode && manualValues.current_unit_price > 0 && (() => {
                        const distUnitPrice = liveCalculation.distribution_unit_price || 0;
                        const energyOfferPrice = ((ptfPrice / 1000 + (liveCalculation.include_yekdem ? yekdemPrice / 1000 : 0)) * multiplier);
                        
                        let offerPrice: number;
                        let label: string;
                        let currentPrice = manualValues.current_unit_price;
                        
                        if (offerDisplayMode === 'combined') {
                          // Toplam: PTF + YEKDEM + Dağıtım hepsi bir arada
                          offerPrice = energyOfferPrice + distUnitPrice;
                          label = 'Birim Fiyat — Toplam (TL/kWh)';
                          currentPrice = manualValues.current_unit_price + distUnitPrice;
                        } else {
                          // Enerji: sadece PTF + YEKDEM (dağıtım ayrı satır)
                          offerPrice = energyOfferPrice;
                          label = 'Birim Aktif Enerji (TL/kWh)';
                        }
                        
                        return (
                          <tr className="bg-blue-50/50">
                            <td className="py-1 px-2 text-gray-600 italic">{label}</td>
                            <td className="py-1 px-2 text-right text-blue-700 font-medium">{currentPrice.toLocaleString('tr-TR', {minimumFractionDigits: 4, maximumFractionDigits: 4})}</td>
                            <td className="py-1 px-2 text-right text-primary-700 font-medium">{offerPrice.toLocaleString('tr-TR', {minimumFractionDigits: 4, maximumFractionDigits: 4})}</td>
                            <td className={`py-1 px-2 text-right font-medium ${currentPrice > offerPrice ? 'text-green-600' : 'text-red-600'}`}>
                              {(currentPrice - offerPrice).toLocaleString('tr-TR', {minimumFractionDigits: 4, maximumFractionDigits: 4})}
                            </td>
                          </tr>
                        );
                      })()}
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
                      setConsumptionInput('');
                      setCurrentUnitPriceInput('');
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
