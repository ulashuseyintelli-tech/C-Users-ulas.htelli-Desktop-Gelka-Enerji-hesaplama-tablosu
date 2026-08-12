// =============================================================================
// S5 — Outreach — /outreach API client.
// =============================================================================
//
// prospectingApi.ts ile AYNI desen: paylaşılan `api` axios instance + tipli
// async fonksiyonlar. Alan sözleşmesi backend/app/outreach/schemas.py ile
// birebir aynı.
//
// HARD GATE hatırlatması (owner): can_send/BLOCKED burada YALNIZ GÖSTERİM
// içindir — backend her approve/send çağrısında TAZE yeniden değerlendirir,
// bu dosyadaki hiçbir tip/fonksiyon o kontrolü BYPASS ETMEZ.
// =============================================================================

import { api } from '../api';

export type OutreachMessageStatus =
  | 'DRAFT' | 'READY_FOR_REVIEW' | 'APPROVED' | 'SENDING' | 'SENT' | 'FAILED'
  | 'BOUNCED' | 'REPLIED' | 'SUPPRESSED' | 'CANCELLED';

export type RecipientCategory = 'PROSPECT_RECIPIENT' | 'TEST_RECIPIENT';
export type RecipientLegalType = 'TACIR' | 'ESNAF' | 'BIREYSEL' | 'UNKNOWN';

export interface ComplianceSnapshot {
  can_send: boolean;
  reason_codes: string[];
  recipient_category: RecipientCategory;
  contact_type: string | null;
  recipient_legal_type: RecipientLegalType;
  iys_status: string;
  suppression_status: string;
  source_status: string;
  kvkk_status: string;
  normalized_email: string | null;
  evaluated_at: string;
}

export interface OutreachSourceSnapshot {
  company_name: string | null;
  sector: string | null;
  city: string | null;
  contact_full_name: string | null;
  used_ai: boolean;
  template_name: string;
  template_version: number;
  source_urls: string[];
}

export interface OutreachMessageOut {
  id: number;
  prospect_company_id: number | null;
  customer_id: number | null;
  contact_id: number | null;
  recipient_email_snapshot: string;
  recipient_legal_type: RecipientLegalType | null;
  recipient_category: RecipientCategory;
  channel: string;
  subject: string;
  body_snapshot: string;
  system_footer_snapshot: string;
  status: OutreachMessageStatus;
  provider: string | null;
  provider_message_id: string | null;
  approved_at: string | null;
  sent_at: string | null;
  failed_at: string | null;
  failure_code: string | null;
  source_snapshot_json: OutreachSourceSnapshot | null;
  compliance_snapshot_json: ComplianceSnapshot | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SuppressionEntryOut {
  id: number;
  email_normalized: string;
  reason: string;
  source: string | null;
  note: string | null;
  created_at: string | null;
  effective_at: string | null;
}

export type SuppressionReason = 'USER_REJECTED' | 'DO_NOT_CONTACT' | 'PERMANENT_BOUNCE' | 'LEGAL_BLOCK' | 'MANUAL_BLOCK';

/**
 * Taslak oluşturur — HENÜZ gönderilmez. compliance burada yalnız
 * görüntüleme amaçlı (bkz. backend service.py docstring'i).
 *
 * Çağrıldığı yerler:
 * - OutreachPanel.tsx ("Yeni Taslak Oluştur") [S5-WB7]
 */
export async function createDraftMessage(input: {
  prospect_company_id?: number;
  contact_id?: number;
  customer_id?: number;
  use_ai?: boolean;
}): Promise<OutreachMessageOut> {
  const response = await api.post('/outreach/messages', input);
  return response.data;
}

export async function listOutreachMessages(params?: {
  prospect_company_id?: number;
  status?: OutreachMessageStatus;
  skip?: number;
  limit?: number;
}): Promise<OutreachMessageOut[]> {
  const response = await api.get('/outreach/messages', { params });
  return response.data;
}

export async function getOutreachMessage(id: number): Promise<OutreachMessageOut> {
  const response = await api.get(`/outreach/messages/${id}`);
  return response.data;
}

/** UI'nin "güncel compliance durumunu göster" ihtiyacı — mesajın status'unu DEĞİŞTİRMEZ. */
export async function refreshOutreachCompliance(id: number): Promise<OutreachMessageOut> {
  const response = await api.get(`/outreach/messages/${id}/compliance`);
  return response.data;
}

/** Owner: taslak "user-editable". DRAFT → READY_FOR_REVIEW. Yalnız editable blok değişir. */
export async function finalizeDraftMessage(
  id: number,
  fields: { subject?: string; editable_body?: string }
): Promise<OutreachMessageOut> {
  const response = await api.patch(`/outreach/messages/${id}`, fields);
  return response.data;
}

/** READY_FOR_REVIEW → APPROVED. can_send=false ise backend 409 döner. */
export async function approveOutreachMessage(id: number): Promise<OutreachMessageOut> {
  const response = await api.post(`/outreach/messages/${id}/approve`);
  return response.data;
}

/**
 * HIGH PRIORITY — gerçek gönderimi tetikler. Owner: "Onayla hiçbir koşulda
 * otomatik Gönder çalıştırmamalı" — bu HER ZAMAN ayrı, açık bir kullanıcı
 * tıklamasıyla çağrılır.
 */
export async function sendOutreachMessage(id: number): Promise<OutreachMessageOut> {
  const response = await api.post(`/outreach/messages/${id}/send`);
  return response.data;
}

/** Owner: "optional user-opt-in follow-up Task" — otomatik DEĞİL, ayrı bir buton. */
export async function createFollowUpTask(id: number, daysFromNow: number = 3): Promise<{ id: number; title: string; due_at: string | null }> {
  const response = await api.post(`/outreach/messages/${id}/follow-up-task`, null, { params: { days_from_now: daysFromNow } });
  return response.data;
}

export async function createSuppression(input: { email: string; reason: SuppressionReason; note?: string }): Promise<SuppressionEntryOut> {
  const response = await api.post('/outreach/suppressions', input);
  return response.data;
}

export async function listSuppressions(params?: { skip?: number; limit?: number }): Promise<SuppressionEntryOut[]> {
  const response = await api.get('/outreach/suppressions', { params });
  return response.data;
}
