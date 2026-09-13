// =============================================================================
// Fiyat kimliği (sürüm 3, OWNER-KARARI-02 SEG-2) — YEKDEM segmentinin AÇIK seçimi
// =============================================================================
// YEKDEM dahil teklifte segment (Serbest Tüketici / GTŞ-K1) her teklifte, tedarik
// bağlamına göre açıkça seçilir ve teklif snapshot'ında saklanır. Varsayılan YOKTUR;
// iki segmentin değeri eşit olsa bile seçim zorunludur (eşitlik otomatik seçim
// gerekçesi değildir). Doğrulama sunucudadır (dönem + segment için onaylı YEKDEM).
//
// Çağrıldığı yerler:
// - App.tsx → fiyat paneli (YEKDEM 'Dahil' seçiliyken; manuel + AI akışı)
// =============================================================================

import { YEKDEM_SEGMENT_LABELS, type YekdemSegment } from './priceReadiness';

interface YekdemSegmentSelectorProps {
  value: YekdemSegment | null;
  onChange: (segment: YekdemSegment) => void;
  disabled?: boolean;
}

const SIRA: YekdemSegment[] = ['st', 'gts'];

export function YekdemSegmentSelector({ value, onChange, disabled }: YekdemSegmentSelectorProps) {
  return (
    <div
      role="radiogroup"
      aria-label="YEKDEM segmenti"
      className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-700"
    >
      <span className="font-medium">Segment:</span>
      {SIRA.map((seg) => (
        <label key={seg} className="inline-flex items-center gap-1"
          title="Tedarik bağlamına göre açıkça seçin; değerler eşit olsa bile seçim zorunludur">
          <input
            type="radio"
            name="yekdem-segment"
            value={seg}
            checked={value === seg}
            disabled={disabled}
            onChange={() => onChange(seg)}
          />
          {YEKDEM_SEGMENT_LABELS[seg]}
        </label>
      ))}
      {value === null && <span className="text-amber-700">(seçilmedi)</span>}
    </div>
  );
}
