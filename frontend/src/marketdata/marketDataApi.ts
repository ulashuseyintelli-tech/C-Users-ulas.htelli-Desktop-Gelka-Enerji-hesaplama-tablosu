import { API_BASE, pricingGetPeriods, PricingPeriodsResponse } from '../api';

// ═══════════════════════════════════════════════════════════════════════════════
// EPİAŞ Piyasa Verisi (saatlik PTF/SMF) — Yönetim Sayfası API Client
//
// Backend tarafı YENİ DEĞİL — POST /api/pricing/upload-market-data ve
// GET /api/pricing/periods zaten mevcut (Pricing Risk Engine router'ı).
// Bu dosya yalnız o var olan endpoint'lere ince bir istemci sarmalayıcısı
// ekler. pricingGetPeriods/PricingPeriodsResponse burada TEKRAR
// TANIMLANMAZ — ../api'den doğrudan re-export edilir (owner kararı: kod
// tekrarından kaçın).
//
// Çağrıldığı yerler:
// - frontend/src/marketdata/MarketDataPage.tsx → uploadMarketData(), getMarketDataPeriods()
// ═══════════════════════════════════════════════════════════════════════════════

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB — Recon ile aynı sınır
const ALLOWED_EXTENSIONS = ['.xlsx', '.xls'];

export class MarketDataApiError extends Error {
  code: string;
  details?: string;

  constructor(code: string, message: string, details?: string) {
    super(message);
    this.name = 'MarketDataApiError';
    this.code = code;
    this.details = details;
  }
}

export interface RejectedRow {
  row: number;
  reason: string;
}

export interface UploadMarketDataResponse {
  status: string;
  period: string;
  total_rows: number;
  expected_hours: number;
  missing_hours: number[];
  rejected_rows: RejectedRow[];
  warnings: string[];
  quality_score: number;
  version: number;
  previous_version_archived: boolean;
}

function validateFile(file: File): void {
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    throw new MarketDataApiError(
      'INVALID_EXTENSION',
      `Desteklenmeyen dosya formatı: ${ext}. Sadece .xlsx ve .xls dosyaları kabul edilir.`
    );
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    throw new MarketDataApiError(
      'FILE_TOO_LARGE',
      `Dosya boyutu çok büyük (${(file.size / 1024 / 1024).toFixed(1)} MB). Maksimum 50 MB.`
    );
  }
}

/**
 * EPİAŞ Uzlaştırma Dönemi Detayı Excel dosyasını yükler (POST /api/pricing/upload-market-data).
 * Aynı dönem için tekrar yüklenirse önceki versiyon otomatik arşivlenir
 * (backend'in kendi versiyonlama mantığı — bkz. app/pricing/router.py upload_market_data()).
 *
 * Çağrıldığı yerler:
 * - MarketDataPage.tsx handleUpload() → dosya başına bir çağrı (çoklu dosya sırayla işlenir)
 */
export async function uploadMarketData(file: File): Promise<UploadMarketDataResponse> {
  validateFile(file);

  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/api/pricing/upload-market-data`, {
    method: 'POST',
    body: formData,
  });

  if (response.ok) {
    return response.json();
  }

  let errorData: { error?: string; message?: string; warnings?: string[] } = {};
  try {
    errorData = await response.json();
  } catch {
    // Response body JSON değil
  }

  throw new MarketDataApiError(
    errorData.error || `HTTP_${response.status}`,
    errorData.message || `Yükleme başarısız oldu (${response.status}).`,
    errorData.warnings ? errorData.warnings.join(' | ') : undefined
  );
}

/**
 * Yüklü dönemleri getirir (GET /api/pricing/periods). market_data_periods
 * alanı bu sayfanın kapsam tablosunu besler.
 *
 * Çağrıldığı yerler:
 * - MarketDataPage.tsx: sayfa açıldığında + her yükleme sonrası yeniden çağrılır
 */
export async function getMarketDataPeriods(): Promise<PricingPeriodsResponse> {
  return pricingGetPeriods();
}
