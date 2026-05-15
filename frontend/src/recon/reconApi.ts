import { API_BASE } from '../api';
import { ReconReport, ReconRequest } from './types';

// ═══════════════════════════════════════════════════════════════════════════════
// Invoice Reconciliation API Client
// ═══════════════════════════════════════════════════════════════════════════════

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
const ALLOWED_EXTENSIONS = ['.xlsx', '.xls'];

export class ReconApiError extends Error {
  code: string;
  details?: string;

  constructor(code: string, message: string, details?: string) {
    super(message);
    this.name = 'ReconApiError';
    this.code = code;
    this.details = details;
  }
}

function validateFile(file: File): void {
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    throw new ReconApiError(
      'INVALID_EXTENSION',
      `Desteklenmeyen dosya formatı: ${ext}. Sadece .xlsx ve .xls dosyaları kabul edilir.`
    );
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    throw new ReconApiError(
      'FILE_TOO_LARGE',
      `Dosya boyutu çok büyük (${(file.size / 1024 / 1024).toFixed(1)} MB). Maksimum 50 MB.`
    );
  }
}

export async function analyzeRecon(file: File, requestBody?: ReconRequest): Promise<ReconReport> {
  // Client-side validation
  validateFile(file);

  const formData = new FormData();
  formData.append('file', file);

  if (requestBody) {
    formData.append('request_body', JSON.stringify(requestBody));
  }

  const response = await fetch(`${API_BASE}/api/recon/analyze`, {
    method: 'POST',
    body: formData,
  });

  if (response.ok) {
    const data: ReconReport = await response.json();
    // Both "ok" and "partial" are valid success states
    return data;
  }

  // Error handling
  let errorData: { error?: string; message?: string; details?: string } = {};
  try {
    errorData = await response.json();
  } catch {
    // Response body is not JSON
  }

  throw new ReconApiError(
    errorData.error || `HTTP_${response.status}`,
    errorData.message || `Sunucu hatası (HTTP ${response.status})`,
    errorData.details
  );
}
