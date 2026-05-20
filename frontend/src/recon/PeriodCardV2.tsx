/**
 * PeriodCardV2 — v2 Cost-Headline Period Card
 *
 * Hierarchy (top-down):
 *   1. Header bar    — period label + status badge + (optional) data-eksik pill + (i) tooltip
 *   2. Cost Headline — 4-cell grid (Mevcut enerji bedeli, Referans, Fark, Gelka)
 *   3. Accordion     — "Tüketim profili (T1/T2/T3)"   [collapsed]
 *   4. Accordion     — "Mutabakat detayı"             [collapsed, only if data]
 *   5. Block banner  — quote_blocked → amber notice (no red treatment, REQ-5.9)
 *
 * Critical UI rules (lock):
 *   - null ≠ 0. Null cells render "—" with secondary "hesaplanamadı" hint.
 *   - "Mevcut enerji bedeli" comes from declared_total_tl (energy-only),
 *     NOT v1 cost_comparison.invoice_total_tl (energy + distribution).
 *   - Forbidden surface labels (REQ-4.4 / REQ-4.5 — never render in UI):
 *     supplier-margin / true-cost / actual-cost terminology.
 *     Replaced by "Referans maliyet farkı".
 *   - Negative markup wording (REQ-6.3): "negatif fark — fatura referans
 *     maliyetin altında". Color: blue (neutral / advisory), NOT red.
 *   - Negative savings: "Mevcut tedarikçi daha avantajlı görünüyor".
 *     Color: amber (caution), NOT red.
 *   - Partial period (reference_energy_cost_tl === null): all 4 cells get
 *     amber treatment, NEVER red (REQ-5.8/5.9).
 *
 * Accordion state persistence:
 *   - Each accordion holds its own boolean state via useState; open state
 *     survives sibling re-renders. (React preserves component state by
 *     position; no parent prop passing required.)
 */

import { useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Info,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import type { PeriodResult, ReconciliationItem } from './types';
import { formatTL, formatPct, formatKwh, HESAPLANAMADI } from './CostFormat';

// ═══════════════════════════════════════════════════════════════════════════════
// Public component
// ═══════════════════════════════════════════════════════════════════════════════

export interface PeriodCardV2Props {
  period: PeriodResult;
  /**
   * Operator's declared total invoice TL for THIS period (energy-only).
   * Read directly from the request invoices array — backend echoes it back
   * via supplier_markup_tl computation, but headline cell needs the raw value
   * to display when ref cost is null but invoice was provided (REQ-6.1).
   */
  declaredTotalTl?: number | null;
}

export function PeriodCardV2({ period, declaredTotalTl }: PeriodCardV2Props) {
  const refCost = period.reference_energy_cost_tl;
  const isPartial = refCost === null;

  const cardTone = isPartial
    ? 'bg-amber-50/40 border-amber-200'
    : 'bg-white border-slate-200';

  return (
    <article
      className={`rounded-xl border ${cardTone} shadow-sm overflow-hidden`}
      aria-label={`Dönem ${period.period}`}
    >
      <PeriodHeader period={period} />
      <CostHeadline
        period={period}
        declaredTotalTl={declaredTotalTl ?? null}
      />
      <ConsumptionAccordion period={period} />
      <ReconciliationAccordion period={period} />
      {period.quote_blocked && (
        <QuoteBlockedBanner reason={period.quote_block_reason} />
      )}
    </article>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Header bar — period + status + data completeness pill + tooltip
// ═══════════════════════════════════════════════════════════════════════════════

function PeriodHeader({ period }: { period: PeriodResult }) {
  const refCost = period.reference_energy_cost_tl;
  const isPartial = refCost === null;

  // Status badge color — EXISTING v1 semantic preserved.
  const statusBadge =
    period.overall_severity === 'CRITICAL'
      ? 'bg-amber-500 text-white'
      : period.overall_severity === 'WARNING'
        ? 'bg-amber-400 text-white'
        : 'bg-emerald-500 text-white';

  return (
    <header className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-100 bg-gradient-to-r from-slate-50 to-white">
      <h3 className="text-base font-bold text-slate-800">{period.period}</h3>

      <span
        className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${statusBadge}`}
      >
        {period.overall_status}
      </span>

      {isPartial && (
        <span
          className="text-[10px] px-2 py-0.5 rounded-full font-semibold bg-amber-100 text-amber-800 border border-amber-300"
          title="Referans maliyet hesaplanamadı (PTF veya YEKDEM eksik)"
        >
          Veri eksik
        </span>
      )}

      <CostInputsTooltip period={period} />
    </header>
  );
}

function CostInputsTooltip({ period }: { period: PeriodResult }) {
  const ci = period.cost_inputs;
  const tooltipText = [
    `PTF kaynağı: ${ci.ptf_source}`,
    `YEKDEM kaynağı: ${ci.yekdem_source}`,
    `Dönem aralığı: ${ci.period_start} → ${ci.period_end}`,
    `Saat kapsamı: ${ci.total_hours}`,
    `Tamlık: ${ci.complete ? 'tam' : 'eksik'}`,
  ].join('\n');

  return (
    <span
      className="ml-auto inline-flex items-center text-slate-400 hover:text-slate-600 cursor-help"
      title={tooltipText}
      aria-label="Veri kaynağı detayları"
    >
      <Info className="w-3.5 h-3.5" />
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Cost Headline — 4 cells, primary surface
// ═══════════════════════════════════════════════════════════════════════════════

function CostHeadline({
  period,
  declaredTotalTl,
}: {
  period: PeriodResult;
  declaredTotalTl: number | null;
}) {
  const refCost = period.reference_energy_cost_tl;
  const markupTl = period.supplier_markup_tl;
  const markupPct = period.supplier_markup_pct;
  const gelkaEstimate = period.gelka_estimate_tl;
  const savings = period.potential_savings_tl;
  const isPartial = refCost === null;

  // Container amber overlay when ref cost is null (REQ-5.8 + REQ-5.9 — never red)
  const headlineContainer = isPartial
    ? 'bg-gradient-to-r from-amber-50 to-amber-50/40'
    : 'bg-gradient-to-r from-blue-50/40 to-violet-50/40';

  return (
    <div className={`px-4 py-3 ${headlineContainer}`}>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <HeadlineCell
          title="Mevcut enerji bedeli"
          subtitle="Fatura beyan toplamı (TL)"
          value={formatTL(declaredTotalTl)}
          isMissing={declaredTotalTl === null || declaredTotalTl === undefined}
          missingHint={
            declaredTotalTl === null || declaredTotalTl === undefined
              ? 'Fatura tutarı girilmedi'
              : undefined
          }
        />

        <HeadlineCell
          title="Referans enerji maliyeti"
          subtitle="EPİAŞ PTF + YEKDEM bazlı"
          value={formatTL(refCost)}
          isMissing={refCost === null}
          missingHint={isPartial ? HESAPLANAMADI : undefined}
          variant={isPartial ? 'partial' : 'reference'}
        />

        <MarkupCell markupTl={markupTl} markupPct={markupPct} />

        <GelkaCell
          gelkaEstimate={gelkaEstimate}
          savings={savings}
          refCostMissing={isPartial}
        />
      </div>

      {/* When ref cost null but declared total exists — explanatory note (REQ-6.2) */}
      {isPartial && declaredTotalTl !== null && declaredTotalTl !== undefined && (
        <p className="mt-2 text-xs text-amber-800 italic">
          Referans enerji maliyeti hesaplanamadı — fatura tutarı gösterimi sınırlı.
        </p>
      )}
    </div>
  );
}

interface HeadlineCellProps {
  title: string;
  subtitle?: string;
  value: string;
  isMissing?: boolean;
  missingHint?: string;
  variant?: 'reference' | 'partial' | 'positive' | 'negative-fark' | 'negative-savings' | 'neutral';
  secondary?: React.ReactNode;
}

function HeadlineCell({
  title,
  subtitle,
  value,
  isMissing,
  missingHint,
  variant = 'neutral',
  secondary,
}: HeadlineCellProps) {
  // Color palette — neutral/advisory tones; NEVER red.
  // Positive markup/fark = slate (neutral), negative fark = blue (advisory),
  // partial/missing = amber (caution but not error).
  const valueColorMap: Record<NonNullable<HeadlineCellProps['variant']>, string> = {
    reference: 'text-blue-700',
    partial: 'text-amber-700',
    positive: 'text-slate-800',           // markup positive → neutral
    'negative-fark': 'text-blue-700',     // markup negative → blue (advisory)
    'negative-savings': 'text-amber-700', // savings negative → amber (caution)
    neutral: 'text-slate-800',
  };

  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">
        {title}
      </span>
      <span
        className={`text-lg font-bold tabular-nums ${valueColorMap[variant]} ${isMissing ? 'opacity-70' : ''}`}
      >
        {value}
      </span>
      {subtitle && (
        <span className="text-[10px] text-slate-500">{subtitle}</span>
      )}
      {missingHint && (
        <span className="text-[10px] text-amber-700 italic">{missingHint}</span>
      )}
      {secondary}
    </div>
  );
}

function MarkupCell({
  markupTl,
  markupPct,
}: {
  markupTl: number | null;
  markupPct: number | null;
}) {
  const isMissing = markupTl === null;
  const isNegative = markupTl !== null && markupTl < 0;

  let variant: HeadlineCellProps['variant'] = 'neutral';
  if (!isMissing) {
    variant = isNegative ? 'negative-fark' : 'positive';
  }

  let secondary: React.ReactNode = null;
  if (!isMissing) {
    secondary = (
      <div className="flex items-center gap-1">
        <span className="text-xs font-medium text-slate-600 tabular-nums">
          {formatPct(markupPct)}
        </span>
        {isNegative && (
          // REQ-6.3 — neutral wording, no judgmental qualifier (REQ-6.4).
          <span className="text-[10px] text-blue-700 italic">
            negatif fark — fatura referans maliyetin altında
          </span>
        )}
      </div>
    );
  }

  return (
    <HeadlineCell
      title="Referans maliyet farkı"
      subtitle="Fatura − referans (TL)"
      value={formatTL(markupTl)}
      isMissing={isMissing}
      missingHint={isMissing ? HESAPLANAMADI : undefined}
      variant={variant}
      secondary={secondary}
    />
  );
}

function GelkaCell({
  gelkaEstimate,
  savings,
  refCostMissing,
}: {
  gelkaEstimate: number | null;
  savings: number | null;
  refCostMissing: boolean;
}) {
  const isMissing = gelkaEstimate === null;

  let secondary: React.ReactNode = null;
  if (savings !== null) {
    if (savings > 0) {
      secondary = (
        <div className="flex items-center gap-1 text-xs font-medium">
          <TrendingDown className="w-3 h-3 text-emerald-600" aria-hidden />
          <span className="text-emerald-700 tabular-nums">
            {formatTL(savings)}
          </span>
          <span className="text-[10px] text-slate-500">olası tasarruf</span>
        </div>
      );
    } else if (savings < 0) {
      // Neutral, advisory wording — NEVER red.
      secondary = (
        <div className="flex items-center gap-1 text-xs font-medium">
          <TrendingUp className="w-3 h-3 text-amber-600" aria-hidden />
          <span className="text-amber-700 tabular-nums">
            {formatTL(savings)}
          </span>
          <span className="text-[10px] text-amber-700 italic">
            mevcut tedarikçi daha avantajlı görünüyor
          </span>
        </div>
      );
    } else {
      secondary = (
        <span className="text-[10px] text-slate-500 italic">
          fark yok — eşit
        </span>
      );
    }
  }

  return (
    <HeadlineCell
      title="Gelka teklif tahmini"
      subtitle="Referans × marj çarpanı"
      value={formatTL(gelkaEstimate)}
      isMissing={isMissing}
      missingHint={isMissing && refCostMissing ? HESAPLANAMADI : undefined}
      variant="reference"
      secondary={secondary}
    />
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Accordion: Tüketim profili (T1/T2/T3) — collapsed by default (REQ-3.2)
// ═══════════════════════════════════════════════════════════════════════════════

function ConsumptionAccordion({ period }: { period: PeriodResult }) {
  // useState in this component preserves accordion open state across
  // parent re-renders (React keeps component state by tree position).
  const [open, setOpen] = useState(false);

  return (
    <Accordion
      open={open}
      onToggle={() => setOpen((v) => !v)}
      label="Tüketim profili (T1/T2/T3)"
      ariaControls={`consumption-${period.period}`}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-4 pb-3">
        <ConsumptionCell
          tone="amber"
          label="T1 Gündüz"
          kwh={period.t1_kwh}
          pct={period.t1_pct}
        />
        <ConsumptionCell
          tone="rose"
          label="T2 Puant"
          kwh={period.t2_kwh}
          pct={period.t2_pct}
        />
        <ConsumptionCell
          tone="indigo"
          label="T3 Gece"
          kwh={period.t3_kwh}
          pct={period.t3_pct}
        />
        <ConsumptionCell
          tone="slate"
          label="Toplam"
          kwh={period.total_kwh}
          pct={null}
        />
      </div>
      {(period.missing_hours > 0 || period.duplicate_hours > 0) && (
        <div className="px-4 pb-3 text-xs text-amber-700">
          {period.missing_hours > 0 && (
            <span>
              {period.missing_hours} eksik saat
              {period.duplicate_hours > 0 ? ' · ' : ''}
            </span>
          )}
          {period.duplicate_hours > 0 && (
            <span>{period.duplicate_hours} duplike saat</span>
          )}
        </div>
      )}
    </Accordion>
  );
}

function ConsumptionCell({
  tone,
  label,
  kwh,
  pct,
}: {
  tone: 'amber' | 'rose' | 'indigo' | 'slate';
  label: string;
  kwh: number | null;
  pct: number | null;
}) {
  const toneClass = {
    amber: 'text-amber-700',
    rose: 'text-rose-700',
    indigo: 'text-indigo-700',
    slate: 'text-slate-700',
  }[tone];

  return (
    <div className="flex flex-col">
      <span className={`text-[10px] uppercase tracking-wide font-semibold ${toneClass}`}>
        {label}
      </span>
      <span className="text-sm font-bold text-slate-800 tabular-nums">
        {formatKwh(kwh)}
      </span>
      {pct !== null && (
        <span className="text-[10px] text-slate-500 tabular-nums">
          {formatPct(pct)}
        </span>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Accordion: Mutabakat detayı — collapsed by default (REQ-3.3)
// ═══════════════════════════════════════════════════════════════════════════════

function ReconciliationAccordion({ period }: { period: PeriodResult }) {
  const [open, setOpen] = useState(false);
  const hasItems = period.reconciliation.length > 0;
  const hasWarnings = period.warnings.length > 0;
  const hasCostComparison = !!period.cost_comparison;

  if (!hasItems && !hasWarnings && !hasCostComparison) {
    return null; // nothing to disclose
  }

  return (
    <Accordion
      open={open}
      onToggle={() => setOpen((v) => !v)}
      label="Mutabakat detayı"
      ariaControls={`recon-${period.period}`}
    >
      <div className="px-4 pb-3 space-y-2">
        {hasItems && <ReconciliationItems items={period.reconciliation} />}
        {hasCostComparison && period.cost_comparison && (
          <CostComparisonSummary comparison={period.cost_comparison} />
        )}
        {hasWarnings && (
          <ul className="text-xs text-amber-700 space-y-0.5">
            {period.warnings.map((w, i) => (
              <li key={i}>⚠ {w}</li>
            ))}
          </ul>
        )}
      </div>
    </Accordion>
  );
}

function ReconciliationItems({ items }: { items: ReconciliationItem[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-xs w-full">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-slate-500">
            <th className="pr-4 py-1">Alan</th>
            <th className="pr-4 py-1 text-right">Excel</th>
            <th className="pr-4 py-1 text-right">Fatura</th>
            <th className="pr-4 py-1 text-right">Δ kWh</th>
            <th className="pr-4 py-1 text-right">Δ %</th>
            <th className="py-1">Durum</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            // No red — keep advisory tones consistent with headline.
            const sevTone =
              item.severity === 'CRITICAL'
                ? 'bg-amber-200 text-amber-900'
                : item.severity === 'WARNING'
                  ? 'bg-amber-100 text-amber-800'
                  : item.severity === 'LOW'
                    ? 'bg-emerald-100 text-emerald-800'
                    : 'bg-slate-100 text-slate-700';

            return (
              <tr key={i} className="border-t border-slate-100">
                <td className="pr-4 py-1 font-medium text-slate-700 uppercase">
                  {item.field}
                </td>
                <td className="pr-4 py-1 text-right tabular-nums text-slate-700">
                  {formatKwh(item.excel_total_kwh)}
                </td>
                <td className="pr-4 py-1 text-right tabular-nums text-slate-700">
                  {formatKwh(item.invoice_total_kwh)}
                </td>
                <td className="pr-4 py-1 text-right tabular-nums text-slate-700">
                  {formatKwh(item.delta_kwh)}
                </td>
                <td className="pr-4 py-1 text-right tabular-nums text-slate-700">
                  {formatPct(item.delta_pct)}
                </td>
                <td className="py-1">
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${sevTone}`}
                  >
                    {item.status}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CostComparisonSummary({
  comparison,
}: {
  comparison: NonNullable<PeriodResult['cost_comparison']>;
}) {
  return (
    <div className="rounded-md bg-slate-50 border border-slate-200 px-3 py-2 text-xs">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mb-1">
        Maliyet karşılaştırması (enerji + dağıtım)
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 tabular-nums text-slate-700">
        <div>
          <span className="text-slate-500">Fatura toplam: </span>
          <span className="font-semibold">
            {formatTL(comparison.invoice_total_tl)}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Gelka toplam: </span>
          <span className="font-semibold">
            {formatTL(comparison.gelka_total_tl)}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Fark: </span>
          <span
            className={`font-semibold ${comparison.diff_tl >= 0 ? 'text-emerald-700' : 'text-blue-700'}`}
          >
            {formatTL(comparison.diff_tl)} ({formatPct(comparison.diff_pct)})
          </span>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Quote-blocked banner — bottom of card (amber, never red)
// ═══════════════════════════════════════════════════════════════════════════════

function QuoteBlockedBanner({ reason }: { reason: string | null }) {
  return (
    <div className="bg-amber-100/60 border-t border-amber-200 px-4 py-2 flex items-center gap-2 text-xs">
      <AlertTriangle className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" aria-hidden />
      <span className="font-medium text-amber-800">
        Bu dönem için referans maliyet hesaplanamadı.
      </span>
      {reason && (
        <span className="text-amber-700 truncate" title={reason}>
          — {reason}
        </span>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Generic accordion (state-preserving via local useState in caller)
// ═══════════════════════════════════════════════════════════════════════════════

function Accordion({
  open,
  onToggle,
  label,
  ariaControls,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  label: string;
  ariaControls: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-slate-100">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={ariaControls}
        className="w-full px-4 py-2 flex items-center justify-between text-left hover:bg-slate-50 transition-colors"
      >
        <span className="text-[11px] uppercase tracking-wide text-slate-600 font-semibold">
          {label}
        </span>
        {open ? (
          <ChevronUp className="w-4 h-4 text-slate-400" aria-hidden />
        ) : (
          <ChevronDown className="w-4 h-4 text-slate-400" aria-hidden />
        )}
      </button>
      {open && (
        <div id={ariaControls} className="border-t border-slate-100">
          {children}
        </div>
      )}
    </section>
  );
}
