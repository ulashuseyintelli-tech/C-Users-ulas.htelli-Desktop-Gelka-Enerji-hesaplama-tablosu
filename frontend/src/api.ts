import axios from 'axios';

const API_BASE = 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface AnalyzeResponse {
  extraction: {
    vendor: string;
    invoice_period: string;
    consumption_kwh: { value: number; confidence: number };
    current_active_unit_price_tl_per_kwh: { value: number; confidence: number };
    distribution_unit_price_tl_per_kwh: { value: number; confidence: number };
    invoice_total_with_vat_tl: { value: number; confidence: number };
  };
  validation: {
    is_ready_for_pricing: boolean;
    missing_fields: string[];
    errors: string[];
    warnings: string[];
  };
}

export interface CalculateResponse {
  current_energy_tl: number;
  current_distribution_tl: number;
  current_btv_tl: number;
  current_vat_matrah_tl: number;
  current_vat_tl: number;
  current_total_with_vat_tl: number;
  offer_energy_tl: number;
  offer_distribution_tl: number;
  offer_btv_tl: number;
  offer_vat_matrah_tl: number;
  offer_vat_tl: number;
  offer_total_with_vat_tl: number;
  difference_excl_vat_tl: number;
  difference_incl_vat_tl: number;
  savings_ratio: number;
  // Birim fiyatlar
  current_distribution_unit_tl_per_kwh?: number;
  offer_distribution_unit_tl_per_kwh?: number;
  // Meta fields
  meta_include_yekdem_in_offer?: boolean;
  meta_extra_items_apply_to_offer?: boolean;
  meta_use_offer_distribution?: boolean;
  meta_consumption_kwh?: number;
  // Dağıtım kaynağı bilgisi
  meta_distribution_source?: string;  // "epdk_tariff:sanayi/OG/çift_terim", "manual_override", "extracted_from_invoice"
  meta_distribution_tariff_key?: string;  // "sanayi/OG/çift_terim"
  meta_distribution_mismatch_warning?: string;
  // PTF/YEKDEM kaynağı bilgisi
  meta_pricing_source?: string;  // "reference", "override", "default"
  meta_pricing_period?: string;  // "2025-01"
  meta_ptf_tl_per_mwh?: number;
  meta_yekdem_tl_per_mwh?: number;
  // KDV oranı
  meta_vat_rate?: number;  // 0.20 = %20, 0.10 = %10
}

export interface DebugMeta {
  trace_id: string;
  pricing_period?: string;
  pricing_source?: string;
  ptf_tl_per_mwh?: number;
  yekdem_tl_per_mwh?: number;
  epdk_tariff_key?: string;
  distribution_unit_price_tl_per_kwh?: number;
  distribution_source?: string;
  consumption_kwh?: number;
  energy_amount_tl?: number;
  distribution_amount_tl?: number;
  btv_amount_tl?: number;
  kdv_amount_tl?: number;
  total_amount_tl?: number;
  warnings?: string[];
  errors?: string[];
  llm_model_used?: string;
  llm_raw_output_truncated?: string;
  json_repair_applied?: boolean;
  extraction_cache_hit?: boolean;
}

export interface FullProcessResponse {
  extraction: AnalyzeResponse['extraction'];
  validation: AnalyzeResponse['validation'];
  calculation: CalculateResponse | null;
  calculation_error?: string;  // Hesaplama hatası mesajı
  quality_score?: QualityScore;  // Sprint 3: Kalite skoru
  debug_meta?: DebugMeta;
  meta?: {
    trace_id: string;
    fast_mode: boolean;
    model: string;
  };
}

export async function analyzeInvoice(file: File): Promise<AnalyzeResponse> {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await api.post('/analyze-invoice', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function fullProcess(
  file: File,
  params: {
    weighted_ptf_tl_per_mwh?: number;
    yekdem_tl_per_mwh?: number;
    agreement_multiplier?: number;
    use_reference_prices?: boolean;
  }
): Promise<FullProcessResponse> {
  const formData = new FormData();
  formData.append('file', file);
  
  // use_reference_prices: true = DB'den çek, false = verilen değerleri kullan
  const useRef = params.use_reference_prices !== false;
  
  // Query params olarak gönder (backend Query() ile alıyor)
  const queryParams = new URLSearchParams();
  queryParams.append('use_reference_prices', useRef.toString());
  
  if (!useRef && params.weighted_ptf_tl_per_mwh) {
    queryParams.append('weighted_ptf_tl_per_mwh', params.weighted_ptf_tl_per_mwh.toString());
  }
  if (!useRef && params.yekdem_tl_per_mwh) {
    queryParams.append('yekdem_tl_per_mwh', params.yekdem_tl_per_mwh.toString());
  }
  if (params.agreement_multiplier) {
    queryParams.append('agreement_multiplier', params.agreement_multiplier.toString());
  }
  
  const response = await api.post(`/full-process?${queryParams.toString()}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function healthCheck(): Promise<{ status: string }> {
  const response = await api.get('/health');
  return response.data;
}

export async function generateOfferPdf(
  extraction: FullProcessResponse['extraction'],
  calculation: CalculateResponse,
  params: {
    weighted_ptf_tl_per_mwh: number;
    yekdem_tl_per_mwh: number;
    agreement_multiplier: number;
  },
  customerName?: string,
  contactPerson?: string,
  offerDate?: string,
  offerValidityDays?: number,
  tariffGroup?: string
): Promise<Blob> {
  const formData = new FormData();
  
  // Params
  formData.append('weighted_ptf_tl_per_mwh', params.weighted_ptf_tl_per_mwh.toString());
  formData.append('yekdem_tl_per_mwh', params.yekdem_tl_per_mwh.toString());
  formData.append('agreement_multiplier', params.agreement_multiplier.toString());
  
  // Extraction data
  formData.append('consumption_kwh', (extraction.consumption_kwh?.value || 0).toString());
  formData.append('current_unit_price', (extraction.current_active_unit_price_tl_per_kwh?.value || 0).toString());
  formData.append('distribution_unit_price', (extraction.distribution_unit_price_tl_per_kwh?.value || 0).toString());
  formData.append('invoice_total', (calculation.current_total_with_vat_tl || 0).toString());
  formData.append('vendor', extraction.vendor || 'unknown');
  formData.append('invoice_period', extraction.invoice_period || '');
  
  // Tarife grubu
  if (tariffGroup) {
    formData.append('tariff_group', tariffGroup);
  }
  
  // Calculation data - Mevcut fatura
  formData.append('current_energy_tl', calculation.current_energy_tl.toString());
  formData.append('current_distribution_tl', calculation.current_distribution_tl.toString());
  formData.append('current_btv_tl', calculation.current_btv_tl.toString());
  formData.append('current_vat_tl', calculation.current_vat_tl.toString());
  formData.append('current_vat_matrah_tl', calculation.current_vat_matrah_tl.toString());
  formData.append('current_total_with_vat_tl', calculation.current_total_with_vat_tl.toString());
  
  // Calculation data - Teklif
  formData.append('offer_energy_tl', calculation.offer_energy_tl.toString());
  formData.append('offer_distribution_tl', calculation.offer_distribution_tl.toString());
  formData.append('offer_btv_tl', calculation.offer_btv_tl.toString());
  formData.append('offer_vat_tl', calculation.offer_vat_tl.toString());
  formData.append('offer_vat_matrah_tl', calculation.offer_vat_matrah_tl.toString());
  formData.append('offer_total', calculation.offer_total_with_vat_tl.toString());
  
  // Fark ve tasarruf
  formData.append('difference_incl_vat_tl', calculation.difference_incl_vat_tl.toString());
  formData.append('savings_ratio', calculation.savings_ratio.toString());
  
  // KDV oranı
  if (calculation.meta_vat_rate !== undefined) {
    formData.append('vat_rate', calculation.meta_vat_rate.toString());
  }
  
  // Müşteri bilgileri
  if (customerName) {
    formData.append('customer_name', customerName);
  }
  if (contactPerson) {
    formData.append('contact_person', contactPerson);
  }
  if (offerDate) {
    formData.append('offer_date', offerDate);
  }
  if (offerValidityDays) {
    formData.append('offer_validity_days', offerValidityDays.toString());
  }
  
  const response = await api.post('/generate-pdf-simple', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    responseType: 'blob',
  });
  return response.data;
}


// ═══════════════════════════════════════════════════════════════════════════════
// ADMIN API
// ═══════════════════════════════════════════════════════════════════════════════

// Admin API key - SADECE MEMORY'DE (localStorage'a YAZMA!)
let adminApiKey = '';

export function setAdminApiKey(key: string) {
  adminApiKey = key;
  // GÜVENLİK: localStorage'a YAZMA!
}

export function getAdminApiKey(): string {
  return adminApiKey;
}

export function clearAdminApiKey() {
  adminApiKey = '';
}

// Admin axios instance
export const adminApi = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Admin key interceptor
adminApi.interceptors.request.use((config) => {
  if (adminApiKey) {
    config.headers['X-Admin-Key'] = adminApiKey;
  }
  return config;
});

// ═══════════════════════════════════════════════════════════════════════════════
// Market Prices (PTF/YEKDEM)
// ═══════════════════════════════════════════════════════════════════════════════

/** @deprecated Yeni market-prices modülündeki types.ts kullanın: MarketPriceRecord */
export interface MarketPrice {
  period: string;
  ptf_tl_per_mwh: number;
  yekdem_tl_per_mwh: number;
  source: string;
  is_locked: boolean;
}

/** @deprecated Yeni market-prices modülündeki types.ts kullanın: MarketPricesListResponse */
export interface MarketPricesResponse {
  status: string;
  count: number;
  prices: MarketPrice[];
}

/** @deprecated Yeni API client: market-prices/marketPricesApi.ts → listMarketPrices() */
export async function getMarketPrices(limit: number = 24): Promise<MarketPricesResponse> {
  const response = await adminApi.get(`/admin/market-prices?limit=${limit}`);
  return response.data;
}

/** @deprecated Yeni API client: market-prices/marketPricesApi.ts → listMarketPrices() */
export async function getMarketPrice(period: string): Promise<MarketPrice> {
  const response = await adminApi.get(`/admin/market-prices/${period}`);
  return response.data;
}

/** @deprecated Yeni API client: market-prices/marketPricesApi.ts → upsertMarketPrice() */
export async function upsertMarketPrice(
  period: string,
  ptf_tl_per_mwh: number,
  yekdem_tl_per_mwh: number,
  source_note?: string
): Promise<{ status: string; message: string }> {
  const formData = new FormData();
  formData.append('period', period);
  formData.append('ptf_tl_per_mwh', ptf_tl_per_mwh.toString());
  formData.append('yekdem_tl_per_mwh', yekdem_tl_per_mwh.toString());
  if (source_note) {
    formData.append('source_note', source_note);
  }
  
  const response = await adminApi.post('/admin/market-prices', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

/** @deprecated Lock/unlock artık yeni modülde yönetilmiyor. Backward compat için korunuyor. */
export async function lockMarketPrice(period: string): Promise<{ status: string; message: string }> {
  const response = await adminApi.post(`/admin/market-prices/${period}/lock`);
  return response.data;
}

/** @deprecated Lock/unlock artık yeni modülde yönetilmiyor. Backward compat için korunuyor. */
export async function unlockMarketPrice(period: string): Promise<{ status: string; message: string }> {
  const response = await adminApi.post(`/admin/market-prices/${period}/unlock`);
  return response.data;
}

// ═══════════════════════════════════════════════════════════════════════════════
// Distribution Tariffs (EPDK)
// ═══════════════════════════════════════════════════════════════════════════════

export interface DistributionTariff {
  tariff_group: string;
  voltage_level: string;
  term_type: string;
  unit_price_tl_per_kwh: number;
  key: string;
}

export interface DistributionTariffsResponse {
  status: string;
  count: number;
  tariffs: DistributionTariff[];
  note: string;
}

export async function getDistributionTariffs(): Promise<DistributionTariffsResponse> {
  const response = await adminApi.get('/admin/distribution-tariffs');
  return response.data;
}

export interface TariffLookupResult {
  status: string;
  success: boolean;
  unit_price_tl_per_kwh: number | null;
  tariff_key: string | null;
  normalized: {
    tariff_group: string;
    voltage_level: string;
    term_type: string;
  };
  error_message?: string;
}

export async function lookupDistributionTariff(
  tariff_group: string,
  voltage_level: string,
  term_type: string
): Promise<TariffLookupResult> {
  const params = new URLSearchParams({
    tariff_group,
    voltage_level,
    term_type,
  });
  const response = await adminApi.get(`/admin/distribution-tariffs/lookup?${params}`);
  return response.data;
}


// ═══════════════════════════════════════════════════════════════════════════════
// Quality Score (Sprint 3)
// ═══════════════════════════════════════════════════════════════════════════════

export interface QualityFlagDetail {
  code: string;
  severity: string;
  message: string;
  deduction: number;
}

export interface QualityScore {
  score: number;
  grade: 'OK' | 'CHECK' | 'BAD';
  flags: string[];
  flag_details: QualityFlagDetail[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// Incidents (Sprint 3)
// ═══════════════════════════════════════════════════════════════════════════════

export interface Incident {
  id: number;
  trace_id: string;
  tenant_id: string;
  invoice_id?: string;
  offer_id?: number;
  severity: 'S1' | 'S2' | 'S3' | 'S4';
  category: string;
  message: string;
  details?: Record<string, any>;
  status: 'OPEN' | 'ACK' | 'RESOLVED';
  resolution_note?: string;
  resolved_by?: string;
  resolved_at?: string;
  created_at: string;
  // Dedupe alanları (Sprint 4)
  occurrence_count?: number;
  first_seen_at?: string;
  last_seen_at?: string;
}

export interface IncidentsResponse {
  status: string;
  count: number;
  incidents: Incident[];
}

export interface IncidentStatsResponse {
  status: string;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  total: number;
}

export async function getIncidents(params?: {
  status?: string;
  severity?: string;
  category?: string;
  limit?: number;
}): Promise<IncidentsResponse> {
  const queryParams = new URLSearchParams();
  if (params?.status) queryParams.append('status', params.status);
  if (params?.severity) queryParams.append('severity', params.severity);
  if (params?.category) queryParams.append('category', params.category);
  if (params?.limit) queryParams.append('limit', params.limit.toString());
  
  const response = await adminApi.get(`/admin/incidents?${queryParams}`);
  return response.data;
}

export async function getIncident(id: number): Promise<Incident> {
  const response = await adminApi.get(`/admin/incidents/${id}`);
  return response.data;
}

export async function updateIncidentStatus(
  id: number,
  status: 'OPEN' | 'ACK' | 'RESOLVED',
  resolution_note?: string,
  resolved_by?: string
): Promise<{ status: string; message: string }> {
  const formData = new FormData();
  formData.append('status', status);
  if (resolution_note) formData.append('resolution_note', resolution_note);
  if (resolved_by) formData.append('resolved_by', resolved_by);
  
  const response = await adminApi.patch(`/admin/incidents/${id}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function getIncidentStats(): Promise<IncidentStatsResponse> {
  const response = await adminApi.get('/admin/incidents/stats');
  return response.data;
}

// ═══════════════════════════════════════════════════════════════════════════════
// EPİAŞ Piyasa Fiyatları (PTF/YEKDEM)
// ═══════════════════════════════════════════════════════════════════════════════

export interface EpiasPricesResponse {
  period: string;
  ptf_tl_per_mwh: number | null;
  yekdem_tl_per_mwh: number | null;
  source: string;
  source_description: string;
  is_locked?: boolean;
}

/**
 * Belirli bir dönem için PTF/YEKDEM fiyatlarını çek.
 * 
 * Öncelik sırası:
 * 1. DB'deki kayıt
 * 2. EPİAŞ API (auto_fetch=true ise)
 * 3. Default değerler
 * 
 * @param period Dönem (YYYY-MM format, örn: "2025-01")
 * @param autoFetch EPİAŞ'tan otomatik çek (default: true)
 */
export async function getEpiasPrices(period: string, autoFetch: boolean = true): Promise<EpiasPricesResponse> {
  const response = await api.get(`/api/epias/prices/${period}?auto_fetch=${autoFetch}`);
  return response.data;
}

/**
 * EPİAŞ'tan belirli dönem için veri çek ve cache'le.
 * 
 * @param period Dönem (YYYY-MM format)
 * @param forceRefresh Mevcut cache'i yoksay
 * @param useMock Mock veri kullan (test için)
 */
export async function syncEpiasPrices(
  period: string, 
  forceRefresh: boolean = false,
  useMock: boolean = false
): Promise<{
  status: string;
  period: string;
  ptf_tl_per_mwh?: number;
  yekdem_tl_per_mwh?: number;
  source?: string;
  message: string;
}> {
  const params = new URLSearchParams();
  if (forceRefresh) params.append('force_refresh', 'true');
  if (useMock) params.append('use_mock', 'true');
  
  const response = await api.post(`/api/epias/sync/${period}?${params}`);
  return response.data;
}

/**
 * Fatura dönemini YYYY-MM formatına çevir.
 * 
 * Desteklenen formatlar:
 * - "11/2025" → "2025-11"
 * - "2025-11" → "2025-11"
 * - "Kasım 2025" → "2025-11"
 * - "KASIM 2025" → "2025-11"
 * - "11.2025" → "2025-11"
 */
export function normalizeInvoicePeriod(period: string): string | null {
  if (!period) return null;
  
  const trimmed = period.trim();
  
  // Zaten YYYY-MM formatında mı?
  if (/^\d{4}-\d{2}$/.test(trimmed)) {
    return trimmed;
  }
  
  // MM/YYYY formatı (11/2025)
  const slashMatch = trimmed.match(/^(\d{1,2})\/(\d{4})$/);
  if (slashMatch) {
    const month = slashMatch[1].padStart(2, '0');
    const year = slashMatch[2];
    return `${year}-${month}`;
  }
  
  // MM.YYYY formatı (11.2025)
  const dotMatch = trimmed.match(/^(\d{1,2})\.(\d{4})$/);
  if (dotMatch) {
    const month = dotMatch[1].padStart(2, '0');
    const year = dotMatch[2];
    return `${year}-${month}`;
  }
  
  // Türkçe ay isimleri
  const turkishMonths: Record<string, string> = {
    'ocak': '01', 'şubat': '02', 'mart': '03', 'nisan': '04',
    'mayıs': '05', 'haziran': '06', 'temmuz': '07', 'ağustos': '08',
    'eylül': '09', 'ekim': '10', 'kasım': '11', 'aralık': '12',
  };
  
  // "Kasım 2025" veya "KASIM 2025" formatı
  const turkishMatch = trimmed.toLowerCase().match(/^([a-zğüşıöç]+)\s*(\d{4})$/);
  if (turkishMatch) {
    const monthName = turkishMatch[1];
    const year = turkishMatch[2];
    const month = turkishMonths[monthName];
    if (month) {
      return `${year}-${month}`;
    }
  }
  
  return null;
}
