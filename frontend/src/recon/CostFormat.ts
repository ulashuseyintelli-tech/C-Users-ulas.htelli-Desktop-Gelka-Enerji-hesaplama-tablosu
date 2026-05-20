// ═══════════════════════════════════════════════════════════════════════════════
// Cost Format Helpers — v2 Period Card
// ═══════════════════════════════════════════════════════════════════════════════
//
// Critical UI rule: null ≠ 0.
//   - "0,00 ₺" is a valid computed result; the supplier sold at zero markup.
//   - "—" (placeholder) means "not computed", typically because PTF/YEKDEM
//     was missing for the period. Operator MUST be able to distinguish these.
//
// Backend already rounds with ROUND_HALF_UP at the Decimal_Boundary
// (report_builder.py: _round_currency_tl_half_up / _round_pct_half_up).
// FE does NOT re-round — only formats. Avoids double-rounding drift.
// ═══════════════════════════════════════════════════════════════════════════════

export const TL_PLACEHOLDER = '—';
export const PCT_PLACEHOLDER = '—';
export const KWH_PLACEHOLDER = '—';
export const HESAPLANAMADI = 'hesaplanamadı';

const tlFormatter = new Intl.NumberFormat('tr-TR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const pctFormatter = new Intl.NumberFormat('tr-TR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const kwhFormatter = new Intl.NumberFormat('tr-TR', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
});

/**
 * Format a TL currency value. null/undefined → placeholder dash.
 *
 * IMPORTANT: never returns "0 ₺" for null/undefined. "0,00 ₺" is a valid
 * computed value distinct from "—" (not computed).
 */
export function formatTL(value: number | null | undefined): string {
  if (value === null || value === undefined) return TL_PLACEHOLDER;
  return `${tlFormatter.format(value)} ₺`;
}

/**
 * Format a percentage value. null/undefined → placeholder dash.
 */
export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return PCT_PLACEHOLDER;
  return `%${pctFormatter.format(value)}`;
}

/**
 * Format a kWh value with 3 decimal places. null/undefined → placeholder.
 */
export function formatKwh(value: number | null | undefined): string {
  if (value === null || value === undefined) return KWH_PLACEHOLDER;
  return `${kwhFormatter.format(value)} kWh`;
}
