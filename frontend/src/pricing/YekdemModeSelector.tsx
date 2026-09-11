// =============================================================================
// Fiyat Doğruluğu Faz 1 — YEKDEM uygulamasının AÇIK seçimi (dahil / hariç)
// =============================================================================
// Doğrulanmış bir muafiyet kuralı olmadığı için "muaf" seçeneği yoktur. Gerçek 0
// bir seçim değil, bir değerdir (yetkili ekrandan açıkça girilir). Faturada
// YEKDEM kalemi yoksa (AI akışı) seçim boş gelir ve kullanıcı açıkça seçer;
// "hariç" tahmin edilmez.
//
// Çağrıldığı yerler:
// - App.tsx → fiyat paneli (manuel + AI akışı)
// =============================================================================

import { YEKDEM_MODE_LABELS, type YekdemMode } from './priceReadiness';

interface YekdemModeSelectorProps {
  value: YekdemMode | null;
  onChange: (mode: YekdemMode) => void;
  disabled?: boolean;
}

const SIRA: YekdemMode[] = ['included', 'excluded'];

const ACIKLAMA: Record<YekdemMode, string> = {
  included: 'YEKDEM enerji birim fiyatına eklenir (dönemin kesin değeri gerekir)',
  excluded: 'YEKDEM enerji birim fiyatına eklenmez',
};

export function YekdemModeSelector({ value, onChange, disabled }: YekdemModeSelectorProps) {
  return (
    <div
      role="radiogroup"
      aria-label="YEKDEM uygulaması"
      className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-700"
    >
      <span className="font-medium">YEKDEM:</span>
      {SIRA.map((mod) => (
        <label key={mod} className="inline-flex items-center gap-1" title={ACIKLAMA[mod]}>
          <input
            type="radio"
            name="yekdem-mode"
            value={mod}
            checked={value === mod}
            disabled={disabled}
            onChange={() => onChange(mod)}
          />
          {YEKDEM_MODE_LABELS[mod]}
        </label>
      ))}
      {value === null && <span className="text-amber-700">(seçilmedi)</span>}
    </div>
  );
}
