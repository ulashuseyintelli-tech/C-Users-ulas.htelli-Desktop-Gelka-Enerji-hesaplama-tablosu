// =============================================================================
// PTF Admin Frontend — Constants
// =============================================================================

import type { ListParams } from './types';

// ---------------------------------------------------------------------------
// Backend Error Code → UI Message Mapping
// ---------------------------------------------------------------------------

/**
 * Maps all 13 backend error codes to Turkish UI messages and optional field targets.
 * Codes with a `field` property trigger inline form errors;
 * codes without `field` are displayed as global toast notifications.
 */
export const ERROR_CODE_MAP: Record<string, { message: string; field?: string }> = {
  INVALID_PERIOD_FORMAT: { message: 'Geçersiz dönem formatı (YYYY-MM bekleniyor)', field: 'period' },
  FUTURE_PERIOD: { message: 'Gelecek dönem girilemez', field: 'period' },
  INVALID_PTF_VALUE: { message: 'Geçersiz PTF değeri', field: 'value' },
  INVALID_STATUS: { message: 'Geçersiz durum değeri', field: 'status' },
  INVALID_DECIMAL_FORMAT: { message: 'Geçersiz ondalık format (nokta kullanın)', field: 'value' },
  PERIOD_LOCKED: { message: 'Bu dönem kilitli, güncelleme yapılamaz', field: 'period' },
  FINAL_RECORD_PROTECTED: { message: 'Kesinleşmiş kayıt force_update olmadan güncellenemez', field: 'force_update' },
  STATUS_DOWNGRADE_FORBIDDEN: { message: 'Durum geri alınamaz (final → provisional)', field: 'status' },
  PERIOD_NOT_FOUND: { message: 'Dönem bulunamadı' },
  PARSE_ERROR: { message: 'Dosya ayrıştırma hatası' },
  BATCH_VALIDATION_FAILED: { message: 'Toplu doğrulama başarısız' },
  EMPTY_FILE: { message: 'Dosya boş' },
  CHANGE_REASON_REQUIRED: { message: 'Değişiklik nedeni zorunlu', field: 'change_reason' },
};

// ---------------------------------------------------------------------------
// Default List Parameters
// ---------------------------------------------------------------------------

/** Default parameters for the market prices list API endpoint. */
export const DEFAULT_LIST_PARAMS: ListParams = {
  page: 1,
  page_size: 20,
  sort_by: 'period',
  sort_order: 'desc',
  price_type: 'PTF',
};

// ---------------------------------------------------------------------------
// Status Labels (Turkish)
// ---------------------------------------------------------------------------

/** Maps backend status values to Turkish display labels. */
export const STATUS_LABELS: Record<'provisional' | 'final', string> = {
  provisional: 'Ön Değer',
  final: 'Kesinleşmiş',
};

// ---------------------------------------------------------------------------
// Page Size Options
// ---------------------------------------------------------------------------

/** Available page size options for the price list table pagination. */
export const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
