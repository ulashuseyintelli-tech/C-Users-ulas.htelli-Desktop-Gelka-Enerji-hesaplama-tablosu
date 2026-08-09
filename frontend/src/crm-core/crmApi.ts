// =============================================================================
// S2 — Activity & Task Engine — /crm API client
// =============================================================================
//
// Bkz. backend/app/crm/schemas.py için tam alan sözleşmesi. Aynı desen:
// paylaşılan `api` axios instance + tipli async fonksiyonlar (contractsApi.ts
// ile aynı yaklaşım).
// =============================================================================

import { api } from '../api';

export type ActivityType = 'NOTE' | 'CALL' | 'EMAIL' | 'MEETING' | 'TASK_COMPLETED';
export type TaskStatus = 'OPEN' | 'COMPLETED' | 'CANCELLED';

// Subject: tam olarak biri dolu olmalı (customer_id | offer_id | contract_id)
export interface Subject {
  customer_id?: number;
  offer_id?: number;
  contract_id?: number;
}

export interface ActivityOut {
  id: number;
  customer_id: number | null;
  offer_id: number | null;
  contract_id: number | null;
  activity_type: string;
  title: string | null;
  body: string | null;
  occurred_at: string;
  created_at: string;
  // "manual" → kullanıcı kaydı; "audit" → audit_logs projection (yalnız
  // offer subject'inde, OFFER_STATUS_CHANGED) — activities tablosuna
  // duplicate yazılmadığının UI'a yansıyan kanıtı.
  source: 'manual' | 'audit';
}

export interface TaskOut {
  id: number;
  customer_id: number | null;
  offer_id: number | null;
  contract_id: number | null;
  title: string;
  description: string | null;
  due_at: string | null;
  status: TaskStatus;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodayResponse {
  due_today_count: number;
  overdue_count: number;
  due_today_tasks: TaskOut[];
  overdue_tasks: TaskOut[];
  recent_activities: ActivityOut[];
  total_customers: number;
  total_open_offers: number;
  total_accepted_offers: number;
  total_finalized_contracts: number;
}

/**
 * Çağrıldığı yerler:
 * - CrmCore/CustomerDetailScreen.tsx (Aktiviteler alt-sekmesi) → Not/
 *   Arama/E-posta/Toplantı Kaydet [WB-6]
 * - CrmCore/OffersScreen.tsx, ContractsScreen.tsx → "Not Ekle" [WB-7]
 */
export async function createActivity(
  subject: Subject,
  activityType: Exclude<ActivityType, 'TASK_COMPLETED'>,
  title?: string,
  body?: string,
  occurredAt?: string
): Promise<ActivityOut> {
  const response = await api.post('/crm/activities', {
    ...subject,
    activity_type: activityType,
    title,
    body,
    occurred_at: occurredAt,
  });
  return response.data;
}

/**
 * Çağrıldığı yerler:
 * - CrmCore/CustomerDetailScreen.tsx (Aktiviteler alt-sekmesi, kronolojik
 *   timeline) [WB-6]
 */
export async function listActivities(
  subject: Subject,
  params?: { skip?: number; limit?: number }
): Promise<ActivityOut[]> {
  const response = await api.get('/crm/activities', { params: { ...subject, ...params } });
  return response.data;
}

/**
 * Çağrıldığı yerler:
 * - CrmCore/CustomerDetailScreen.tsx (Görevler alt-sekmesi, yeni görev) [WB-6]
 * - CrmCore/OffersScreen.tsx, ContractsScreen.tsx → "Takip Görevi Oluştur" [WB-7]
 */
export async function createTask(
  subject: Subject,
  title: string,
  description?: string,
  dueAt?: string
): Promise<TaskOut> {
  const response = await api.post('/crm/tasks', {
    ...subject,
    title,
    description,
    due_at: dueAt,
  });
  return response.data;
}

/**
 * Çağrıldığı yerler:
 * - CrmCore/CustomerDetailScreen.tsx (Görevler alt-sekmesi) [WB-6]
 * - CrmCore/TodayScreen.tsx (bugünkü/gecikmiş görev listeleri) [WB-7]
 */
export async function listTasks(params?: {
  customer_id?: number;
  offer_id?: number;
  contract_id?: number;
  status?: TaskStatus;
  due_today?: boolean;
  overdue?: boolean;
  skip?: number;
  limit?: number;
}): Promise<TaskOut[]> {
  const response = await api.get('/crm/tasks', { params });
  return response.data;
}

export async function updateTask(
  taskId: number,
  fields: { title?: string; description?: string; due_at?: string | null }
): Promise<TaskOut> {
  const response = await api.patch(`/crm/tasks/${taskId}`, fields);
  return response.data;
}

/**
 * Idempotent — ikinci çağrı no-op döner (backend garantisi, bkz.
 * app/crm/service.py complete_task()).
 *
 * Çağrıldığı yerler:
 * - CrmCore/TodayScreen.tsx, CustomerDetailScreen.tsx → "Tamamlandı" quick
 *   action [WB-6/WB-7]
 */
export async function completeTask(taskId: number): Promise<TaskOut> {
  const response = await api.post(`/crm/tasks/${taskId}/complete`);
  return response.data;
}

export async function cancelTask(taskId: number): Promise<TaskOut> {
  const response = await api.post(`/crm/tasks/${taskId}/cancel`);
  return response.data;
}

/**
 * S2 "Bugün" projeksiyonu — ayrı tablo değil, sorgu zamanında türetilir.
 *
 * Çağrıldığı yerler:
 * - CrmCore/TodayScreen.tsx → S2 gerçek Bugün ekranı (S1 placeholder'ının
 *   yerini alır) [WB-7]
 */
export async function getToday(): Promise<TodayResponse> {
  const response = await api.get('/crm/today');
  return response.data;
}
