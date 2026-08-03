// =============================================================================
// Sözleşme Hazırla — /api/contracts API client
// =============================================================================
//
// JSON/multipart client for the contract-generation V1 backend
// (app/contracts/router.py). Bkz. market-prices/marketPricesApi.ts için aynı
// desen: paylaşılan `api` axios instance + tipli async fonksiyonlar.
// =============================================================================

import { api, API_BASE, isRunningInElectron } from '../api';

export type DocumentType = 'vergi_levhasi' | 'imza_sirkusu';

export interface DocumentUploadResponse {
  document_id: number;
  document_type: DocumentType;
  processing_status: string;
  sha256: string;
  is_duplicate: boolean;
}

export interface ExtractionStartResponse {
  extraction_run_id: number;
  document_id: number;
  status: string;
}

export interface FieldCandidate {
  id: number;
  field_name: string;
  document_id: number;
  source_document: DocumentType;
  raw_value: string | null;
  normalized_value: string | null;
  confidence: number;
  source_page: number;
  source_text: string | null;
  validation_status: string; // pending | confirmed | overridden | rejected
  conflict_status: string;   // none | conflict | resolved
  user_decision: string | null; // accepted | corrected | rejected
  corrected_value: string | null;
}

export interface ExtractionResultResponse {
  extraction_run_id: number;
  document_id: number;
  document_type: DocumentType;
  status: string;
  error_code: string | null;
  candidates: FieldCandidate[];
}

export interface ContractOut {
  id: number;
  customer_id: number | null;
  offer_id: number;
  contract_number: string | null;
  status: string;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
}

export interface ContractPreviewResponse {
  contract_id: number;
  status: string;
  rendered_html_sections: string[]; // [ana sözleşme, ek protokol, tahliye, bilgilendirme]
}

export interface ContractFinalizeResponse {
  contract_id: number;
  status: string;
  pdf_storage_ref: string;
  pdf_sha256: string;
  contract_number: string | null;
}

export async function uploadContractDocument(
  documentType: DocumentType,
  file: File,
  customerId?: number
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await api.post('/api/contracts/documents/upload', formData, {
    params: { document_type: documentType, ...(customerId ? { customer_id: customerId } : {}) },
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function startContractExtraction(documentId: number): Promise<ExtractionStartResponse> {
  const response = await api.post(`/api/contracts/documents/${documentId}/extract`);
  return response.data;
}

export async function getContractExtractionResult(documentId: number): Promise<ExtractionResultResponse> {
  const response = await api.get(`/api/contracts/documents/${documentId}/extraction-result`);
  return response.data;
}

export async function detectContractConflicts(documentIds: number[]): Promise<{ changed_candidates: FieldCandidate[] }> {
  const response = await api.post('/api/contracts/documents/detect-conflicts', { document_ids: documentIds });
  return response.data;
}

export async function resolveContractCandidate(
  candidateId: number,
  decision: 'accepted' | 'corrected' | 'rejected',
  correctedValue?: string,
  decidedBy?: string
): Promise<FieldCandidate> {
  const response = await api.post(`/api/contracts/candidates/${candidateId}/resolve`, {
    candidate_id: candidateId,
    decision,
    corrected_value: correctedValue,
    decided_by: decidedBy,
  });
  return response.data;
}

export interface SaveLegalProfileRequest {
  customer_id?: number;
  legal_name: string;
  tax_number: string;
  tax_office: string;
  mersis_number?: string;
  trade_registry_number?: string;
  registered_address: string;
  facility_address?: string;
  notification_address?: string;
}

export async function saveLegalProfile(req: SaveLegalProfileRequest): Promise<{ id: number }> {
  const response = await api.post('/api/contracts/legal-profiles', req);
  return response.data;
}

export interface SaveRepresentativeRequest {
  customer_id?: number;
  legal_profile_id?: number;
  full_name: string;
  national_id: string;
  authority_type?: string;
  authority_scope?: string;
  authority_start_date?: string;
  authority_end_date?: string;
  is_indefinite: boolean;
  source_document_id?: number;
}

export async function saveRepresentative(req: SaveRepresentativeRequest): Promise<{ id: number }> {
  const response = await api.post('/api/contracts/representatives', req);
  return response.data;
}

export async function createContractDraft(
  offerId: number,
  customerId?: number,
  legalProfileId?: number,
  authorizedRepresentativeId?: number
): Promise<ContractOut> {
  const response = await api.post('/api/contracts/drafts', {
    offer_id: offerId,
    customer_id: customerId,
    legal_profile_id: legalProfileId,
    authorized_representative_id: authorizedRepresentativeId,
  });
  return response.data;
}

export interface ContractCompleteFields {
  start_date: string; // ISO date
  duration_months: number;
  tariff_group?: string;
  subscription_codes?: string;
}

export async function previewContract(contractId: number, fields: ContractCompleteFields): Promise<ContractPreviewResponse> {
  const response = await api.post(`/api/contracts/${contractId}/preview`, fields);
  return response.data;
}

export async function finalizeContract(contractId: number): Promise<ContractFinalizeResponse> {
  const response = await api.post(`/api/contracts/${contractId}/finalize`);
  return response.data;
}

/**
 * Sözleşme PDF'ini indir — downloadPdf (api.ts) ile aynı desen: Electron'da
 * native "Farklı Kaydet" dialogu (IPC), web'de fetch+blob.
 */
export async function downloadContractPdf(contractId: number, fileName: string): Promise<void> {
  const endpointUrl = `${API_BASE}/api/contracts/${contractId}/download`;

  if (isRunningInElectron()) {
    const result = await window.electronAPI!.downloadFile(endpointUrl, fileName);
    if (result.canceled) return;
    if (!result.ok) throw new Error(result.error || 'Sözleşme PDF indirilemedi.');
    return;
  }

  const response = await fetch(endpointUrl);
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const errorData = await response.json();
      throw new Error(errorData?.detail?.message || errorData?.detail || 'Sözleşme PDF indirilemedi.');
    }
    throw new Error(`Sunucu hatası (${response.status}).`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
