/**
 * MultiPeriodSummaryV2 — Top-of-results summary block + annual projection.
 *
 * Renders ONLY when ReconReport.summary is non-null (i.e., multiple periods).
 *
 * Layout:
 *   ┌ Toplam Özet
 *   │   4 large numbers (Toplam fatura, Toplam referans, Toplam fark, Olası tasarruf)
 *   │   Sub-line: "X / Y dönem hesaplanabildi"
 *   ├ Tahmini yıllık projeksiyon  (only if summary.annual_projection !== null)
 *   │   3 numbers (annualized ref, annualized fark, annualized savings)
 *   │   Disclaimer: "{N} dönem verisinden ekstrapole edilmiştir. Gerçek yıllık değil."
 *
 * Critical rules:
 *   - null ≠ 0 — every total_* field that is null renders "—" + "hesaplanamadı".
 *   - annual_projection.label is the literal locked string from backend
 *     ("tahmini yıllık projeksiyon"). This component renders it AS-IS without
 *     reformatting so any drift in the contract is immediately visible.
 *   - based_on_periods MUST be disclosed. Without it the projection is
 *     misleading.
 */

import type { MultiPeriodSummary } from './types';
import { formatTL, HESAPLANAMADI } from './CostFormat';

export interface MultiPeriodSummaryV2Props {
  summary: MultiPeriodSummary;
}

export function MultiPeriodSummaryV2({ summary }: MultiPeriodSummaryV2Props) {
  return (
    <div className="space-y-2">
      <SummaryTotals summary={summary} />
      {summary.annual_projection && (
        <AnnualProjectionCard summary={summary} />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Top totals — 4 large numbers
// ═══════════════════════════════════════════════════════════════════════════════

function SummaryTotals({ summary }: { summary: MultiPeriodSummary }) {
  // Note: we use total_invoice_tl as the "fatura toplamı" surface here,
  // matching the operator's existing mental model for the multi-period sum
  // (this is the v1 field that aggregates across cost_comparison; if no
  // periods had cost_comparison, it will be 0 — that's a v1 semantic, kept
  // unchanged on the v1 surface). v2 surfaces add their own sums alongside.
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <header className="px-4 py-2 border-b border-slate-100">
        <h2 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">
          Toplam Özet
        </h2>
      </header>

      <div className="px-4 py-3 grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryStat
          title="Toplam fatura"
          subtitle="Enerji + dağıtım (v1)"
          value={summary.total_invoice_tl}
        />
        <SummaryStat
          title="Toplam referans"
          subtitle="EPİAŞ PTF + YEKDEM"
          value={summary.total_reference_energy_cost_tl}
          variant="reference"
        />
        <SummaryStat
          title="Toplam referans maliyet farkı"
          subtitle="Fatura − referans"
          value={summary.total_supplier_markup_tl}
        />
        <SummaryStat
          title="Toplam olası tasarruf"
          subtitle="Fatura − Gelka tahmini"
          value={summary.total_potential_savings_tl}
          variant="savings"
        />
      </div>

      <CoverageDisclosure summary={summary} />
    </section>
  );
}

function SummaryStat({
  title,
  subtitle,
  value,
  variant = 'neutral',
}: {
  title: string;
  subtitle: string;
  value: number | null;
  variant?: 'neutral' | 'reference' | 'savings';
}) {
  const isMissing = value === null || value === undefined;

  const valueColor = (() => {
    if (isMissing) return 'text-amber-700 opacity-70';
    if (variant === 'reference') return 'text-blue-700';
    if (variant === 'savings') {
      if (value === null) return 'text-slate-800';
      if (value > 0) return 'text-emerald-700';
      if (value < 0) return 'text-amber-700'; // never red
      return 'text-slate-800';
    }
    return 'text-slate-800';
  })();

  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">
        {title}
      </span>
      <span className={`text-lg font-bold tabular-nums ${valueColor}`}>
        {formatTL(value)}
      </span>
      <span className="text-[10px] text-slate-500">{subtitle}</span>
      {isMissing && (
        <span className="text-[10px] text-amber-700 italic">
          {HESAPLANAMADI}
        </span>
      )}
    </div>
  );
}

function CoverageDisclosure({ summary }: { summary: MultiPeriodSummary }) {
  const { valid_period_count, partial_period_count, total_period_count } =
    summary;

  return (
    <div className="px-4 pb-3 flex items-center gap-2 text-xs flex-wrap">
      <span className="text-slate-600">
        {valid_period_count} / {total_period_count} dönem hesaplanabildi
      </span>
      {partial_period_count > 0 && (
        <span className="text-amber-700 italic">
          · {partial_period_count} dönem için PTF/YEKDEM eksik
        </span>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Annual projection — separate disclaimer card
// ═══════════════════════════════════════════════════════════════════════════════

function AnnualProjectionCard({ summary }: { summary: MultiPeriodSummary }) {
  // summary.annual_projection is non-null here (parent guarded).
  const ap = summary.annual_projection!;

  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50/60 shadow-sm">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center gap-2">
        <h2 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">
          {/* Backend emits "tahmini yıllık projeksiyon" (locked literal). */}
          {ap.label}
        </h2>
      </header>

      <div className="px-4 py-3 grid grid-cols-1 md:grid-cols-3 gap-3">
        <AnnualStat
          title="Yıllık referans maliyet (tahmini)"
          value={ap.annualized_reference_cost_tl}
          variant="reference"
        />
        <AnnualStat
          title="Yıllık referans maliyet farkı (tahmini)"
          value={ap.annualized_supplier_markup_tl}
        />
        <AnnualStat
          title="Yıllık olası tasarruf (tahmini)"
          value={ap.annualized_potential_savings_tl}
          variant="savings"
        />
      </div>

      <p className="px-4 pb-3 text-[11px] text-slate-500 italic">
        {ap.based_on_periods} dönem verisinden ekstrapole edilmiştir. Gerçek
        yıllık değer değildir.
      </p>
    </section>
  );
}

function AnnualStat({
  title,
  value,
  variant = 'neutral',
}: {
  title: string;
  value: number | null;
  variant?: 'neutral' | 'reference' | 'savings';
}) {
  const isMissing = value === null || value === undefined;

  const valueColor = (() => {
    if (isMissing) return 'text-amber-700 opacity-70';
    if (variant === 'reference') return 'text-blue-700';
    if (variant === 'savings') {
      if (value === null) return 'text-slate-800';
      if (value > 0) return 'text-emerald-700';
      if (value < 0) return 'text-amber-700';
      return 'text-slate-800';
    }
    return 'text-slate-800';
  })();

  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">
        {title}
      </span>
      <span className={`text-base font-bold tabular-nums ${valueColor}`}>
        {formatTL(value)}
      </span>
      {isMissing && (
        <span className="text-[10px] text-amber-700 italic">
          {HESAPLANAMADI}
        </span>
      )}
    </div>
  );
}
