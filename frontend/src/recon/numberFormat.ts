// ═══════════════════════════════════════════════════════════════════════════════
// Turkish Number Format Helpers — Recon FE
// ═══════════════════════════════════════════════════════════════════════════════
//
// TR locale rules:
//   "3,08"          → 3.08      (only comma → decimal)
//   "32.257,08"     → 32257.08  (dots = thousands, comma = decimal)
//   "194.412,847"   → 194412.847
//   "1.21167"       → 1.21167   (single dot, no comma → decimal as-is, per user rule)
//   "1.234.567"     → 1234567   (multiple dots, no comma → all thousands)
//   ""              → undefined (preserve "empty" semantics for optional fields)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Parse a Turkish-format number string into a JS number.
 *
 * Returns `undefined` for empty / blank / unparseable input so that the
 * calling code can keep "optional, omit from request body" semantics.
 *
 * IMPORTANT: never returns NaN. Either a valid finite number or undefined.
 */
export function parseTurkishNumber(input: string | null | undefined): number | undefined {
  if (input === null || input === undefined) return undefined;
  const trimmed = String(input).trim();
  if (trimmed === '') return undefined;

  const hasComma = trimmed.includes(',');
  const dotCount = (trimmed.match(/\./g) || []).length;

  let normalized: string;

  if (hasComma) {
    // Turkish format: dots = thousands, comma = decimal (or just plain comma decimal)
    // "32.257,08" → "32257.08"; "3,08" → "3.08"
    normalized = trimmed.replace(/\./g, '').replace(',', '.');
  } else if (dotCount > 1) {
    // Multiple dots, no comma → all thousands separators
    // "1.234.567" → "1234567"
    normalized = trimmed.replace(/\./g, '');
  } else {
    // Single dot (or no separator): treat as-is.
    // "1.21167" → "1.21167"; "194412" → "194412"
    normalized = trimmed;
  }

  const num = Number(normalized);
  if (!Number.isFinite(num)) return undefined;
  return num;
}
