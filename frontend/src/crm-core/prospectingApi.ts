// =============================================================================
// S4 — Prospecting — /prospects API client.
// =============================================================================
//
// crmApi.ts ile AYNI desen: paylaşılan `api` axios instance + tipli async
// fonksiyonlar. Alan sözleşmesi backend/app/prospecting/schemas.py ile
// birebir aynı.
// =============================================================================

import { api } from '../api';

export type ProspectStatus = 'DISCOVERED' | 'VERIFIED' | 'QUALIFIED' | 'DISQUALIFIED' | 'CONVERTED';

export type QualificationReason =
  | 'sector_fit'
  | 'location_fit'
  | 'has_corporate_contact'
  | 'energy_intensive_signal'
  | 'too_small_unsuitable'
  | 'duplicate'
  | 'other';

export type ContactType =
  | 'GENERAL_CORPORATE'
  | 'DEPARTMENT'
  | 'NAMED_CORPORATE_PERSON'
  | 'PERSONAL_OR_FREE_MAIL'
  | 'OTHER';

export interface ProspectCompanyOut {
  id: number;
  legal_name: string | null;
  trade_name: string | null;
  website: string | null;
  normalized_domain: string | null;
  sector: string | null;
  city: string | null;
  district: string | null;
  industrial_zone: string | null;
  address: string | null;
  phone: string | null;
  status: ProspectStatus;
  qualification_reason: QualificationReason | null;
  qualification_note: string | null;
  duplicate_of_id: number | null;
  customer_id: number | null;
  discovered_at: string | null;
  last_verified_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  contact_count: number;
  source_count: number;
}

export interface ProspectCompanyListResponse {
  items: ProspectCompanyOut[];
  total: number;
}

export interface ProspectContactOut {
  id: number;
  prospect_company_id: number;
  full_name: string | null;
  job_title: string | null;
  email: string | null;
  phone: string | null;
  contact_type: ContactType;
  verification_status: string;
  source_id: number | null;
  created_at: string | null;
}

export interface ProspectSourceOut {
  id: number;
  prospect_company_id: number;
  source_url: string;
  source_type: string;
  source_title: string | null;
  evidence_text: string | null;
  fetch_status: string;
  discovered_at: string | null;
  last_checked_at: string | null;
}

export interface DedupMatchOut {
  company_id: number;
  match_signal: string;
  display_name: string | null;
  website: string | null;
  city: string | null;
}

export interface ProspectCreateResponse {
  dedup_verdict: 'created' | 'exact_duplicate' | 'review_required';
  matches: DedupMatchOut[];
  prospect: ProspectCompanyOut | null;
}

export interface ProspectCompanyCreateInput {
  legal_name?: string;
  trade_name?: string;
  website?: string;
  sector?: string;
  city?: string;
  district?: string;
  industrial_zone?: string;
  address?: string;
  phone?: string;
  source_url?: string;
  source_type?: string;
  force_create_despite_duplicate?: boolean;
}

export interface DiscoverCandidateOut {
  title: string;
  url: string;
  snippet: string;
}

export interface DiscoverResponse {
  status: 'OK' | 'UNAVAILABLE' | 'FETCH_FAILED';
  message: string | null;
  candidates: DiscoverCandidateOut[];
}

export interface EnrichResponse {
  prospect: ProspectCompanyOut;
  pages_fetched: number;
  new_contacts: ProspectContactOut[];
  new_sources: ProspectSourceOut[];
}

export interface CustomerMatchOut {
  customer_id: number;
  name: string;
  company: string | null;
  email: string | null;
}

export interface ConvertResponse {
  status: 'confirmation_required' | 'converted';
  potential_matches: CustomerMatchOut[];
  prospect: ProspectCompanyOut | null;
  customer_id: number | null;
  customer_created: boolean | null;
  activity_created: boolean;
  task_created: boolean;
  warnings: string[];
}

/**
 * DB'ye YAZMAZ — yalnız aday listesi (owner: "Discovery results must NOT
 * be written directly to DB, only after explicit 'Prospect Olarak Kaydet'").
 *
 * Çağrıldığı yerler:
 * - CrmCore/ProspectingScreen.tsx (arama paneli) [S4-WB7]
 */
export async function discoverProspects(keyword: string, city?: string, district?: string): Promise<DiscoverResponse> {
  const response = await api.post('/prospects/discover', { keyword, city, district });
  return response.data;
}

/**
 * Çağrıldığı yerler:
 * - CrmCore/ProspectingScreen.tsx ("Prospect Olarak Kaydet" / manuel ekleme) [S4-WB7]
 */
export async function createProspect(input: ProspectCompanyCreateInput): Promise<ProspectCreateResponse> {
  const response = await api.post('/prospects', input);
  return response.data;
}

export async function listProspects(params?: {
  status?: ProspectStatus;
  city?: string;
  sector?: string;
  search?: string;
  skip?: number;
  limit?: number;
}): Promise<ProspectCompanyListResponse> {
  const response = await api.get('/prospects', { params });
  return response.data;
}

export async function getProspect(id: number): Promise<ProspectCompanyOut> {
  const response = await api.get(`/prospects/${id}`);
  return response.data;
}

export async function updateProspect(id: number, fields: ProspectCompanyCreateInput): Promise<ProspectCompanyOut> {
  const response = await api.put(`/prospects/${id}`, fields);
  return response.data;
}

export async function verifyProspect(id: number): Promise<ProspectCompanyOut> {
  const response = await api.post(`/prospects/${id}/verify`);
  return response.data;
}

/** Owner: black-box auto-disqualify YOK — bu her zaman açık bir kullanıcı aksiyonudur. */
export async function qualifyProspect(id: number, reason: QualificationReason, note?: string): Promise<ProspectCompanyOut> {
  const response = await api.post(`/prospects/${id}/qualify`, { reason, note });
  return response.data;
}

export async function disqualifyProspect(id: number, reason: QualificationReason, note?: string): Promise<ProspectCompanyOut> {
  const response = await api.post(`/prospects/${id}/disqualify`, { reason, note });
  return response.data;
}

/** Bounded website enrichment (max 3 sayfa) — bkz. backend enrichment.py. */
export async function enrichProspect(id: number): Promise<EnrichResponse> {
  const response = await api.post(`/prospects/${id}/enrich`);
  return response.data;
}

export async function convertProspect(
  id: number,
  options?: {
    existing_customer_id?: number;
    force_create_new_customer?: boolean;
    create_activity?: boolean;
    create_first_task?: boolean;
    first_task_title?: string;
    first_task_due_at?: string;
  }
): Promise<ConvertResponse> {
  const response = await api.post(`/prospects/${id}/convert`, options ?? {});
  return response.data;
}

export async function listProspectContacts(id: number): Promise<ProspectContactOut[]> {
  const response = await api.get(`/prospects/${id}/contacts`);
  return response.data;
}

export async function listProspectSources(id: number): Promise<ProspectSourceOut[]> {
  const response = await api.get(`/prospects/${id}/sources`);
  return response.data;
}
